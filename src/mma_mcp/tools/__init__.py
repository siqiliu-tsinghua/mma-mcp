"""Tool registry for mma-mcp.

Each tool module calls ``register()`` at import time to declare its tools.
At startup, ``register_tools(mcp, ctx, enabled)`` selectively binds them
to the FastMCP server based on the user's ``[tools] enabled`` config.

Role-based access control:
  - Each role has a ``RoleRuntime`` with allowed tools and a security filter.
  - The ``_safe_wrapper`` reads ``current_user`` contextvar to determine the
    active role and select the correct filter / tool permission set.
  - A ``_active_filter`` contextvar avoids mutating shared state under
    concurrent requests.
"""

from __future__ import annotations

import contextvars
import functools
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from mma_mcp.config import AppConfig
from mma_mcp.kernel import KernelSession
from mma_mcp.security.filter import ExpressionFilter

if TYPE_CHECKING:
    from mma_mcp.security.registry import CapabilityRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-role runtime data
# ---------------------------------------------------------------------------

@dataclass
class RoleRuntime:
    """Pre-computed runtime data for one role."""
    allowed_tools: frozenset[str]
    expr_filter: ExpressionFilter | None  # None = skip filtering ("none")


# Contextvar: per-request security filter override (set by _safe_wrapper)
_active_filter: contextvars.ContextVar[ExpressionFilter | None] = contextvars.ContextVar(
    "_active_filter", default=None,
)


# ---------------------------------------------------------------------------
# Tool context — shared state passed to every tool function
# ---------------------------------------------------------------------------

class ToolContext:
    """Runtime context available to all tools.

    The kernel is started lazily on first access, so the MCP server can boot
    and register tools without waiting for the Wolfram kernel.
    """

    def __init__(
        self,
        config: AppConfig,
        kernel: KernelSession,
        expr_filter: ExpressionFilter,
        registry: "CapabilityRegistry | None" = None,
        role_runtimes: dict[str, RoleRuntime] | None = None,
    ) -> None:
        self.config = config
        self._kernel = kernel
        self.expr_filter = expr_filter
        self._registry = registry
        self._kernel_ready = False
        self.role_runtimes: dict[str, RoleRuntime] = role_runtimes or {}

    @property
    def kernel(self) -> KernelSession:
        """Lazy kernel start: first tool call triggers kernel boot."""
        if not self._kernel_ready:
            self._kernel.start()  # no-op if already running
            self._kernel_ready = True
            self._refine_whitelist()
        return self._kernel

    def _refine_whitelist(self) -> None:
        """If in whitelist mode, refine the filter with live system symbols."""
        if self._registry is None:
            return
        try:
            system_symbols = self._kernel.get_all_system_symbols()
            self._registry.initialize_system_symbols(system_symbols)
        except Exception:
            logger.warning(
                "Could not fetch system symbols; whitelist uses group files only"
            )
            return

        # Rebuild global default filter
        if self.config.security.mode == "whitelist":
            self.expr_filter = self._registry.build_filter(self.config.security)
            logger.info("Global whitelist refined with live system symbols")

        # Rebuild per-role whitelist filters
        for role_name, runtime in self.role_runtimes.items():
            if runtime.expr_filter is not None and runtime.expr_filter.mode == "whitelist":
                role_conf = self.config.auth.roles.get(role_name)
                if role_conf is not None and role_conf.security == "whitelist":
                    from mma_mcp.config import SecurityConfig
                    sec = SecurityConfig(
                        mode="whitelist",
                        allow_groups=role_conf.allow_groups,
                        extra_blocked=role_conf.extra_blocked,
                        extra_allowed=role_conf.extra_allowed,
                    )
                    runtime.expr_filter = self._registry.build_filter(sec)
                    logger.info("Role %s whitelist refined with live system symbols", role_name)

    def check(self, *expressions: str) -> None:
        """Run security check on one or more expression strings.

        Uses the per-request filter (from contextvar) if set, otherwise
        falls back to the global default filter.
        """
        filt = _active_filter.get() or self.expr_filter
        for expr in expressions:
            filt.check(expr)

    @property
    def timeout(self) -> int:
        return self.config.kernel.timeout

    @property
    def hard_timeout(self) -> int:
        return self.config.kernel.hard_timeout

    @property
    def max_result_size(self) -> int:
        return self.config.kernel.max_result_size

    @property
    def default_format(self) -> str:
        return self.config.kernel.default_format

    def truncate(self, result: str) -> str:
        """Truncate result string if it exceeds max_result_size."""
        limit = self.max_result_size
        if limit > 0 and len(result) > limit:
            return result[:limit] + f"\n\n[Truncated: result was {len(result)} chars, limit is {limit}]"
        return result


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# {name: tool_function}
# tool_function signature: (ctx: ToolContext, **params) -> Any
_REGISTRY: dict[str, Callable] = {}


def register(name: str) -> Callable:
    """Decorator to register a tool function.

    The decorated function must accept ``ctx: ToolContext`` as its first arg.
    Its docstring becomes the MCP tool description.

    Usage::

        @register("evaluate")
        def evaluate(ctx: ToolContext, expression: str, form: str = "TeXForm") -> str:
            ...
    """
    def decorator(fn: Callable) -> Callable:
        _REGISTRY[name] = fn
        return fn
    return decorator


def get_registered() -> dict[str, Callable]:
    """Return a copy of all registered tools."""
    return dict(_REGISTRY)


# ---------------------------------------------------------------------------
# Error handling wrapper
# ---------------------------------------------------------------------------

def _safe_wrapper(fn: Callable, ctx: ToolContext, tool_name: str) -> Callable:
    """Wrap a tool function with role-based access control and error handling.

    The wrapper:
    1. Reads ``current_user`` contextvar to determine the active role.
    2. Checks if the tool is allowed for that role.
    3. Sets ``_active_filter`` contextvar to the role's security filter.
    4. Catches SecurityError / WolframKernelException → user-friendly messages.
    """
    import inspect
    from mma_mcp.kernel import KernelTimeout
    from mma_mcp.security.filter import SecurityError
    from wolframclient.exception import WolframKernelException

    @functools.wraps(fn)
    def wrapper(**kwargs: Any) -> Any:
        try:
            # Role-based access control
            filt_token = _apply_role_policy(ctx, tool_name)
            try:
                return fn(ctx, **kwargs)
            finally:
                if filt_token is not None:
                    _active_filter.reset(filt_token)
        except SecurityError as e:
            logger.warning("Security: %s", e)
            return f"[Security Error] {e}"
        except KernelTimeout as e:
            logger.error("Kernel timeout: %s", e)
            return f"[Timeout] {e}"
        except WolframKernelException as e:
            logger.error("Kernel error: %s", e)
            return f"[Kernel Error] {e}"
        except _AccessDenied as e:
            return str(e)
        except Exception as e:
            logger.exception("Unexpected error in tool %s", fn.__name__)
            return f"[Error] {type(e).__name__}: {e}"

    # Strip the 'ctx' parameter from the signature so FastMCP doesn't see it.
    # Also remove the return annotation — FastMCP handles Image returns
    # specially via its own decorator path, but our dynamic registration
    # confuses pydantic if it sees the Image type in annotations.
    sig = inspect.signature(fn)
    params = [p for name, p in sig.parameters.items() if name != "ctx"]
    wrapper.__signature__ = sig.replace(parameters=params, return_annotation=inspect.Parameter.empty)
    wrapper.__annotations__ = {
        k: v for k, v in fn.__annotations__.items()
        if k != "ctx" and k != "return"
    }

    return wrapper


class _AccessDenied(Exception):
    """Internal exception for role-based access denial."""


def _apply_role_policy(ctx: ToolContext, tool_name: str) -> contextvars.Token | None:
    """Check role permissions and set the active filter. Returns the contextvar token."""
    if not ctx.role_runtimes:
        return None  # No RBAC configured — use global defaults

    from mma_mcp.auth import current_user
    user = current_user.get()

    if not user.role:
        return None  # Anonymous / stdio — use global defaults

    runtime = ctx.role_runtimes.get(user.role)
    if runtime is None:
        raise _AccessDenied(f"[Access Denied] Unknown role: {user.role}")

    if tool_name not in runtime.allowed_tools:
        raise _AccessDenied(
            f"[Access Denied] Tool '{tool_name}' is not available for role '{user.role}'"
        )

    if runtime.expr_filter is not None:
        return _active_filter.set(runtime.expr_filter)

    # expr_filter is None → security = "none", skip filtering
    # Set a permissive blacklist filter (empty blocked set = allow everything)
    return _active_filter.set(ExpressionFilter("blacklist", frozenset()))


# ---------------------------------------------------------------------------
# Bind tools to FastMCP server
# ---------------------------------------------------------------------------

def register_tools(mcp: Any, ctx: ToolContext, enabled: list[str]) -> list[str]:
    """Register enabled tools onto the FastMCP server instance.

    Returns the list of tool names that were actually registered.
    """
    # Ensure tool modules are imported so they call @register
    from mma_mcp.tools import evaluate, math  # noqa: F401

    registered: list[str] = []
    for name in enabled:
        fn = _REGISTRY.get(name)
        if fn is None:
            logger.warning("Tool %r listed in config but not found in registry", name)
            continue
        wrapped = _safe_wrapper(fn, ctx, name)
        mcp.tool(name=name)(wrapped)
        registered.append(name)
        logger.info("Registered tool: %s", name)

    skipped = set(_REGISTRY) - set(enabled)
    if skipped:
        logger.info("Available but not enabled: %s", sorted(skipped))

    return registered
