"""Tool registry for mma-mcp.

Each tool module calls ``register()`` at import time to declare its tools.
At startup, ``register_tools(mcp, ctx, enabled)`` selectively binds them
to the FastMCP server based on the ``[tools] enabled`` config.

Role-based access control:
  - Each role has a ``RoleRuntime`` with allowed tools and a security filter.
  - The ``_safe_wrapper`` reads ``current_client`` contextvar to determine the
    active role and select the correct filter / tool permission set.
  - A ``_active_filter`` contextvar avoids mutating shared state under
    concurrent requests.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable

from mma_mcp.config import AppConfig
from mma_mcp.pool import KernelPool
from mma_mcp.security.filter import ExpressionFilter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-role runtime data
# ---------------------------------------------------------------------------

@dataclass
class RoleRuntime:
    """Pre-computed runtime data for one role."""
    allowed_tools: frozenset[str]
    expr_filter: ExpressionFilter | None  # None = skip filtering ("none")
    # Per-role resource limits (0 = inherit global)
    timeout: int = 0
    hard_timeout: int = 0
    max_result_size: int = 0


# Contextvar: per-request security filter override (set by _safe_wrapper)
_active_filter: contextvars.ContextVar[ExpressionFilter | None] = contextvars.ContextVar(
    "_active_filter", default=None,
)


# ---------------------------------------------------------------------------
# Tool context — shared state passed to every tool function
# ---------------------------------------------------------------------------

class ToolContext:
    """Runtime context available to all tools.

    Holds the kernel worker pool and security configuration.  Tools
    acquire a worker from the pool for each evaluation — no shared
    kernel state between calls.
    """

    def __init__(
        self,
        config: AppConfig,
        pool: KernelPool,
        expr_filter: ExpressionFilter,
        role_runtimes: dict[str, RoleRuntime] | None = None,
    ) -> None:
        self.config = config
        self.pool = pool
        self.expr_filter = expr_filter
        self.role_runtimes: dict[str, RoleRuntime] = role_runtimes or {}

    def check(self, *expressions: str) -> None:
        """Run security check on one or more expression strings.

        Uses the per-request filter (from contextvar) if set, otherwise
        falls back to the global default filter.
        """
        filt = _active_filter.get() or self.expr_filter
        for expr in expressions:
            filt.check(expr)

    def _current_role_runtime(self) -> RoleRuntime | None:
        """Return the RoleRuntime for the current client, or None."""
        if not self.role_runtimes:
            return None
        from mma_mcp.auth import current_client
        client = current_client.get()
        if not client.role:
            return None
        return self.role_runtimes.get(client.role)

    @property
    def timeout(self) -> int:
        rt = self._current_role_runtime()
        if rt and rt.timeout > 0:
            return rt.timeout
        return self.config.kernel.timeout

    @property
    def hard_timeout(self) -> int:
        rt = self._current_role_runtime()
        if rt and rt.hard_timeout > 0:
            return rt.hard_timeout
        return self.config.kernel.hard_timeout

    @property
    def max_result_size(self) -> int:
        rt = self._current_role_runtime()
        if rt and rt.max_result_size > 0:
            return rt.max_result_size
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
    1. Reads ``current_client`` contextvar to determine the active role.
    2. Checks if the tool is allowed for that role.
    3. Sets ``_active_filter`` contextvar to the role's security filter.
    4. Catches SecurityError / WolframKernelException → readable error messages.

    Supports both sync and async tool functions.  When the underlying
    function is async, the wrapper itself is async and ``await``s the call.
    FastMCP's ``Context`` parameter (named ``mcp_ctx``) is automatically
    injected from the keyword arguments if the underlying function accepts it.
    """
    from mma_mcp.kernel import KernelTimeout
    from mma_mcp.security.filter import SecurityError
    from wolframclient.exception import WolframKernelException

    is_async = asyncio.iscoroutinefunction(fn)

    # Detect whether fn accepts an mcp_ctx parameter
    _fn_sig = inspect.signature(fn)
    _accepts_mcp_ctx = "mcp_ctx" in _fn_sig.parameters

    async def _async_body(**kwargs: Any) -> Any:
        from mma_mcp.logging_config import new_request_id, request_id
        rid = new_request_id()
        rid_token = request_id.set(rid)
        logger.info("Tool %s called (params: %s)", tool_name, list(kwargs.keys()))
        try:
            filt_token = _apply_role_policy(ctx, tool_name)
            try:
                # Inject FastMCP Context if the function accepts it
                if _accepts_mcp_ctx:
                    kwargs["mcp_ctx"] = kwargs.pop("ctx", None)
                result = await fn(ctx, **kwargs)
                logger.info("Tool %s completed", tool_name)
                return result
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
        finally:
            request_id.reset(rid_token)

    def _sync_body(**kwargs: Any) -> Any:
        from mma_mcp.logging_config import new_request_id, request_id
        rid = new_request_id()
        rid_token = request_id.set(rid)
        logger.info("Tool %s called (params: %s)", tool_name, list(kwargs.keys()))
        try:
            filt_token = _apply_role_policy(ctx, tool_name)
            try:
                result = fn(ctx, **kwargs)
                logger.info("Tool %s completed", tool_name)
                return result
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
        finally:
            request_id.reset(rid_token)

    wrapper = _async_body if is_async else _sync_body
    functools.update_wrapper(wrapper, fn)

    # Strip 'ctx' and 'mcp_ctx' from the signature so FastMCP doesn't see
    # our internal parameters.  For async tools that accept mcp_ctx, we
    # re-expose it as 'ctx' with type Context so FastMCP injects it.
    # Also remove the return annotation — FastMCP handles Image returns
    # specially via its own decorator path, but our dynamic registration
    # confuses pydantic if it sees the Image type in annotations.
    sig = inspect.signature(fn)
    params = []
    for pname, p in sig.parameters.items():
        if pname == "ctx":
            continue
        if pname == "mcp_ctx":
            # Re-expose as 'ctx' with Context type for FastMCP injection
            from mcp.server.fastmcp import Context
            params.append(
                p.replace(name="ctx", annotation=Context)
            )
            continue
        params.append(p)
    wrapper.__signature__ = sig.replace(
        parameters=params, return_annotation=inspect.Parameter.empty,
    )
    wrapper.__annotations__ = {
        k: v for k, v in fn.__annotations__.items()
        if k not in ("ctx", "mcp_ctx", "return")
    }
    if _accepts_mcp_ctx:
        from mcp.server.fastmcp import Context
        wrapper.__annotations__["ctx"] = Context

    return wrapper


class _AccessDenied(Exception):
    """Internal exception for role-based access denial."""


def _apply_role_policy(ctx: ToolContext, tool_name: str) -> contextvars.Token | None:
    """Check role permissions and set the active filter. Returns the contextvar token."""
    if not ctx.role_runtimes:
        return None  # No RBAC configured — use global defaults

    from mma_mcp.auth import current_client
    client = current_client.get()

    if not client.role:
        return None  # Anonymous / stdio — use global defaults

    runtime = ctx.role_runtimes.get(client.role)
    if runtime is None:
        raise _AccessDenied(f"[Access Denied] Unknown role: {client.role}")

    if tool_name not in runtime.allowed_tools:
        raise _AccessDenied(
            f"[Access Denied] Tool '{tool_name}' is not available for role '{client.role}'"
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
    from mma_mcp.tools import evaluate  # noqa: F401

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
