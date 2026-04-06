"""Unit tests for tools/__init__.py — registry, wrappers, and RBAC."""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from mma_mcp.config import AppConfig, AuthConfig, RoleConfig, UserConfig
from mma_mcp.security.filter import ExpressionFilter, SecurityError
from mma_mcp.tools import (
    RoleRuntime,
    ToolContext,
    _active_filter,
    _apply_role_policy,
    _safe_wrapper,
    get_registered,
    register,
    register_tools,
)


# ===================================================================
# Helpers
# ===================================================================

def _make_kernel_mock() -> MagicMock:
    """Create a mock KernelSession."""
    kernel = MagicMock()
    kernel.evaluate_to_string.return_value = "42"
    kernel.evaluate_to_image.return_value = b"\x89PNG"
    kernel.start.return_value = None
    return kernel


def _make_ctx(
    *,
    mode: str = "blacklist",
    role_runtimes: dict[str, RoleRuntime] | None = None,
) -> ToolContext:
    """Build a ToolContext with a mock kernel and a given security mode."""
    config = AppConfig()
    kernel = _make_kernel_mock()
    blocked = frozenset({"Run", "RunProcess", "DeleteFile"})
    if mode == "blacklist":
        expr_filter = ExpressionFilter("blacklist", blocked)
    else:
        expr_filter = ExpressionFilter("whitelist", frozenset({"Sin", "Cos", "Plus", "x"}))

    ctx = ToolContext(
        config=config,
        kernel=kernel,
        expr_filter=expr_filter,
        role_runtimes=role_runtimes or {},
    )
    # Mark kernel as ready so lazy start doesn't trigger
    ctx._kernel_ready = True
    return ctx


# ===================================================================
# Tool registry
# ===================================================================

class TestToolRegistry:

    def test_get_registered_includes_builtin_tools(self):
        """After importing tool modules, registry contains expected tools."""
        from mma_mcp.tools import evaluate  # noqa: F401
        reg = get_registered()
        assert "evaluate" in reg
        assert "evaluate_image" in reg

    def test_register_decorator(self):
        """@register adds a function to the registry."""
        @register("_test_dummy_tool")
        def dummy(ctx: ToolContext) -> str:
            return "hello"

        reg = get_registered()
        assert "_test_dummy_tool" in reg
        assert reg["_test_dummy_tool"] is dummy


# ===================================================================
# ToolContext.check — security filter delegation
# ===================================================================

class TestToolContextCheck:

    def test_check_passes_safe_expression(self):
        ctx = _make_ctx()
        ctx.check("Sin[x]")  # should not raise

    def test_check_blocks_dangerous_expression(self):
        ctx = _make_ctx()
        with pytest.raises(SecurityError, match="Run"):
            ctx.check("Run[\"ls\"]")

    def test_check_uses_active_filter_contextvar(self):
        """When _active_filter is set, check() uses it instead of the global filter."""
        ctx = _make_ctx()
        # The global filter blocks Run. Set a permissive active filter.
        permissive = ExpressionFilter("blacklist", frozenset())
        token = _active_filter.set(permissive)
        try:
            ctx.check("Run[\"ls\"]")  # should pass with permissive filter
        finally:
            _active_filter.reset(token)


# ===================================================================
# _safe_wrapper — error handling
# ===================================================================

class TestSafeWrapper:

    def test_security_error_returns_message(self):
        ctx = _make_ctx()

        def tool_fn(ctx: ToolContext, expression: str) -> str:
            ctx.check(expression)
            return "ok"

        wrapped = _safe_wrapper(tool_fn, ctx, "test_tool")
        result = wrapped(expression="Run[\"ls\"]")
        assert "[Security Error]" in result

    def test_generic_exception_returns_error(self):
        ctx = _make_ctx()

        def failing_tool(ctx: ToolContext) -> str:
            raise ValueError("boom")

        wrapped = _safe_wrapper(failing_tool, ctx, "test_tool")
        result = wrapped()
        assert "[Error]" in result
        assert "boom" in result

    def test_successful_call(self):
        ctx = _make_ctx()

        def ok_tool(ctx: ToolContext, x: int) -> str:
            return f"result={x}"

        wrapped = _safe_wrapper(ok_tool, ctx, "test_tool")
        result = wrapped(x=42)
        assert result == "result=42"

    def test_wrapper_strips_ctx_from_signature(self):
        import inspect
        ctx = _make_ctx()

        def my_tool(ctx: ToolContext, expression: str, form: str = "tex") -> str:
            return "ok"

        wrapped = _safe_wrapper(my_tool, ctx, "test_tool")
        sig = inspect.signature(wrapped)
        assert "ctx" not in sig.parameters
        assert "expression" in sig.parameters
        assert "form" in sig.parameters


# ===================================================================
# RBAC — _apply_role_policy
# ===================================================================

class TestApplyRolePolicy:

    def _setup_rbac_ctx(self):
        """Build a ctx with two roles: admin (no filter) and reader (restrictive)."""
        admin_runtime = RoleRuntime(
            allowed_tools=frozenset({"evaluate", "evaluate_image"}),
            expr_filter=None,  # security = "none"
        )
        reader_filter = ExpressionFilter(
            "whitelist", frozenset({"Sin", "Cos", "x"}),
        )
        reader_runtime = RoleRuntime(
            allowed_tools=frozenset({"evaluate"}),
            expr_filter=reader_filter,
        )
        return _make_ctx(role_runtimes={
            "admin": admin_runtime,
            "reader": reader_runtime,
        })

    def test_no_rbac_returns_none(self):
        """Without role_runtimes, returns None (use global filter)."""
        ctx = _make_ctx()
        token = _apply_role_policy(ctx, "evaluate")
        assert token is None

    def test_anonymous_user_returns_none(self):
        """Anonymous user (no role) uses global filter."""
        ctx = self._setup_rbac_ctx()
        from mma_mcp.auth import ANONYMOUS, current_user
        tok = current_user.set(ANONYMOUS)
        try:
            result = _apply_role_policy(ctx, "evaluate")
            assert result is None
        finally:
            current_user.reset(tok)

    def test_admin_gets_permissive_filter(self):
        """Admin with security='none' gets an empty blacklist filter."""
        ctx = self._setup_rbac_ctx()
        from mma_mcp.auth import UserIdentity, current_user
        tok = current_user.set(UserIdentity(username="alice", role="admin"))
        try:
            filt_tok = _apply_role_policy(ctx, "evaluate")
            assert filt_tok is not None
            active = _active_filter.get()
            assert active is not None
            assert active.mode == "blacklist"
            # Empty blacklist = everything allowed
            active.check("Run[\"ls\"]")
        finally:
            current_user.reset(tok)
            if filt_tok is not None:
                _active_filter.reset(filt_tok)

    def test_reader_gets_restrictive_filter(self):
        """Reader gets the role-specific whitelist filter."""
        ctx = self._setup_rbac_ctx()
        from mma_mcp.auth import UserIdentity, current_user
        tok = current_user.set(UserIdentity(username="bob", role="reader"))
        try:
            filt_tok = _apply_role_policy(ctx, "evaluate")
            assert filt_tok is not None
            active = _active_filter.get()
            assert active is not None
            assert active.mode == "whitelist"
            with pytest.raises(SecurityError):
                active.check("Run[\"ls\"]")
        finally:
            current_user.reset(tok)
            if filt_tok is not None:
                _active_filter.reset(filt_tok)

    def test_tool_not_allowed_raises(self):
        """Accessing a tool not in allowed_tools raises _AccessDenied."""
        ctx = self._setup_rbac_ctx()
        from mma_mcp.auth import UserIdentity, current_user
        from mma_mcp.tools import _AccessDenied
        tok = current_user.set(UserIdentity(username="bob", role="reader"))
        try:
            with pytest.raises(_AccessDenied):
                _apply_role_policy(ctx, "evaluate_image")  # reader can't use evaluate_image
        finally:
            current_user.reset(tok)

    def test_unknown_role_raises(self):
        """Unknown role raises _AccessDenied."""
        ctx = self._setup_rbac_ctx()
        from mma_mcp.auth import UserIdentity, current_user
        from mma_mcp.tools import _AccessDenied
        tok = current_user.set(UserIdentity(username="eve", role="hacker"))
        try:
            with pytest.raises(_AccessDenied):
                _apply_role_policy(ctx, "evaluate")
        finally:
            current_user.reset(tok)


# ===================================================================
# register_tools — binding to FastMCP
# ===================================================================

class TestRegisterTools:

    def test_registers_enabled_tools(self):
        ctx = _make_ctx()
        mcp = MagicMock()
        # mcp.tool(name=...) should return a decorator that accepts the wrapped fn
        mcp.tool.return_value = lambda fn: fn

        result = register_tools(mcp, ctx, ["evaluate", "evaluate_image"])
        assert "evaluate" in result
        assert "evaluate_image" in result
        assert mcp.tool.call_count == 2

    def test_skips_unknown_tools(self):
        ctx = _make_ctx()
        mcp = MagicMock()
        mcp.tool.return_value = lambda fn: fn

        result = register_tools(mcp, ctx, ["evaluate", "nonexistent_tool"])
        assert "evaluate" in result
        assert "nonexistent_tool" not in result

    def test_empty_enabled_list(self):
        ctx = _make_ctx()
        mcp = MagicMock()
        result = register_tools(mcp, ctx, [])
        assert result == []
        mcp.tool.assert_not_called()


# ===================================================================
# Session isolation
# ===================================================================

class TestSessionIsolation:

    def test_session_context_with_user(self):
        """Authenticated user gets a WL context string."""
        ctx = _make_ctx()
        from mma_mcp.auth import UserIdentity, current_user
        tok = current_user.set(UserIdentity(username="alice", role="admin"))
        try:
            sc = ctx.session_context
            assert sc == "MCP$alice`"
        finally:
            current_user.reset(tok)

    def test_session_context_anonymous(self):
        """Anonymous user gets empty string (no isolation)."""
        ctx = _make_ctx()
        assert ctx.session_context == ""

    def test_session_context_disabled(self):
        """When session_isolation=False, always returns empty."""
        from mma_mcp.config import KernelConfig
        config = AppConfig(kernel=KernelConfig(session_isolation=False))
        kernel = _make_kernel_mock()
        expr_filter = ExpressionFilter("blacklist", frozenset())
        ctx = ToolContext(config=config, kernel=kernel, expr_filter=expr_filter)
        ctx._kernel_ready = True

        from mma_mcp.auth import UserIdentity, current_user
        tok = current_user.set(UserIdentity(username="alice", role="admin"))
        try:
            assert ctx.session_context == ""
        finally:
            current_user.reset(tok)

    def test_sanitize_context_name(self):
        from mma_mcp.kernel import sanitize_context_name
        assert sanitize_context_name("alice") == "MCP$alice`"
        assert sanitize_context_name("Bob123") == "MCP$Bob123`"
        assert sanitize_context_name("a@b.c") == "MCP$abc`"
        assert sanitize_context_name("") == "MCP$anonymous`"
        assert sanitize_context_name("$admin") == "MCP$$admin`"

    def test_wrap_context(self):
        from mma_mcp.kernel import _wrap_context
        assert _wrap_context("1+1", "") == "1+1"
        wrapped = _wrap_context("x = 5", "MCP$alice`")
        assert '$Context = "MCP$alice`"' in wrapped
        assert '$ContextPath' in wrapped
        assert "x = 5" in wrapped


# ===================================================================
# Per-role resource limits
# ===================================================================

class TestRoleResourceLimits:

    def _make_ctx_with_limits(self) -> ToolContext:
        """Build a ctx where admin has custom limits, reader inherits global."""
        admin_runtime = RoleRuntime(
            allowed_tools=frozenset({"evaluate"}),
            expr_filter=None,
            timeout=10,
            hard_timeout=20,
            max_result_size=1024,
        )
        reader_runtime = RoleRuntime(
            allowed_tools=frozenset({"evaluate"}),
            expr_filter=None,
            # all 0 → inherit global
        )
        return _make_ctx(role_runtimes={
            "admin": admin_runtime,
            "reader": reader_runtime,
        })

    def test_role_overrides_timeout(self):
        """Role with timeout > 0 overrides global."""
        ctx = self._make_ctx_with_limits()
        from mma_mcp.auth import UserIdentity, current_user
        tok = current_user.set(UserIdentity(username="alice", role="admin"))
        try:
            assert ctx.timeout == 10
            assert ctx.hard_timeout == 20
            assert ctx.max_result_size == 1024
        finally:
            current_user.reset(tok)

    def test_role_inherits_global_when_zero(self):
        """Role with limit=0 falls back to global config."""
        ctx = self._make_ctx_with_limits()
        from mma_mcp.auth import UserIdentity, current_user
        tok = current_user.set(UserIdentity(username="bob", role="reader"))
        try:
            assert ctx.timeout == ctx.config.kernel.timeout
            assert ctx.hard_timeout == ctx.config.kernel.hard_timeout
            assert ctx.max_result_size == ctx.config.kernel.max_result_size
        finally:
            current_user.reset(tok)

    def test_anonymous_uses_global(self):
        """Anonymous user always gets global limits."""
        ctx = self._make_ctx_with_limits()
        # No current_user set → anonymous
        assert ctx.timeout == ctx.config.kernel.timeout

    def test_truncate_uses_role_limit(self):
        """truncate() respects role-specific max_result_size."""
        ctx = self._make_ctx_with_limits()
        from mma_mcp.auth import UserIdentity, current_user
        tok = current_user.set(UserIdentity(username="alice", role="admin"))
        try:
            big = "x" * 2000
            result = ctx.truncate(big)
            assert len(result) < 2000
            assert "[Truncated" in result
        finally:
            current_user.reset(tok)
