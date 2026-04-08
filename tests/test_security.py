"""Unit tests for security/filter.py and security/registry.py."""

from __future__ import annotations

import pytest

from mma_mcp.security.filter import (
    ExpressionFilter,
    SecurityError,
    extract_symbols,
)
from mma_mcp.security.registry import CapabilityRegistry
from mma_mcp.config import SecurityConfig


# ===================================================================
# extract_symbols
# ===================================================================

class TestExtractSymbols:
    """Tests for the regex-based symbol extractor."""

    def test_simple_expression(self):
        assert extract_symbols("Sin[x] + Cos[y]") >= {"Sin", "Cos", "x", "y"}

    def test_nested_functions(self):
        syms = extract_symbols("Integrate[Sin[x^2], {x, 0, Pi}]")
        assert {"Integrate", "Sin", "x", "Pi"} <= syms

    def test_context_qualified_name(self):
        """System`Run → short name Run."""
        syms = extract_symbols("System`Run[\"ls\"]")
        assert "Run" in syms

    def test_multi_context_qualified(self):
        syms = extract_symbols("Developer`PackedArrayQ[list]")
        assert "PackedArrayQ" in syms

    def test_dollar_symbol(self):
        syms = extract_symbols("$HomeDirectory")
        assert "$HomeDirectory" in syms

    def test_symbol_call_dynamic(self):
        """Symbol["Run"] should be treated as a reference to Run."""
        syms = extract_symbols('Symbol["Run"]')
        assert "Run" in syms

    def test_symbol_call_with_spaces(self):
        syms = extract_symbols('Symbol[ "RunProcess" ]')
        assert "RunProcess" in syms

    def test_get_operator(self):
        """<< is syntactic sugar for Get."""
        syms = extract_symbols('<< "mypackage.wl"')
        assert "Get" in syms

    def test_string_contents_excluded(self):
        """Symbols inside string literals should not be extracted."""
        syms = extract_symbols('"Run is a function"')
        # "Run" appears inside a string, should NOT be in the symbol set
        # (after string stripping, only the empty "" placeholder remains)
        assert "Run" not in syms

    def test_string_with_escaped_quotes(self):
        syms = extract_symbols(r'"He said \"Run\" now"')
        assert "Run" not in syms

    def test_symbol_call_inside_string_still_detected(self):
        """Symbol["X"] is detected BEFORE string stripping."""
        syms = extract_symbols('f[Symbol["DeleteFile"]]')
        assert "DeleteFile" in syms

    def test_empty_expression(self):
        assert extract_symbols("") == set()

    def test_pure_number(self):
        # Numbers are not symbols
        syms = extract_symbols("42 + 3.14")
        assert "42" not in syms

    def test_mixed_operators(self):
        syms = extract_symbols("x + y * z / w - q")
        assert {"x", "y", "z", "w", "q"} <= syms

    # --- Comment stripping ---

    def test_comment_contents_excluded(self):
        """Symbols inside (* ... *) comments should not be extracted."""
        syms = extract_symbols("Sin[x] (* Don't use RunProcess here *)")
        assert "Sin" in syms
        assert "x" in syms
        assert "RunProcess" not in syms

    def test_nested_comments(self):
        """Nested WL comments should be fully stripped."""
        syms = extract_symbols("Cos[y] (* outer (* inner Run *) still comment *)")
        assert "Cos" in syms
        assert "y" in syms
        assert "Run" not in syms

    def test_comment_and_string_together(self):
        """Comments and strings can coexist."""
        syms = extract_symbols('f["hello"] (* Run *) + g[x]')
        assert "f" in syms
        assert "g" in syms
        assert "x" in syms
        assert "Run" not in syms
        assert "hello" not in syms

    def test_empty_comment(self):
        syms = extract_symbols("(**) Sin[x]")
        assert "Sin" in syms


# ===================================================================
# ExpressionFilter — blacklist mode
# ===================================================================

class TestBlacklistFilter:
    """Tests for blacklist-mode ExpressionFilter."""

    @pytest.fixture
    def filt(self):
        blocked = frozenset({"Run", "RunProcess", "SystemOpen", "DeleteFile"})
        return ExpressionFilter("blacklist", blocked)

    def test_safe_expression_passes(self, filt):
        filt.check("Sin[x] + Cos[y]")  # should not raise

    def test_blocked_symbol_raises(self, filt):
        with pytest.raises(SecurityError, match="Run"):
            filt.check("Run[\"ls\"]")

    def test_blocked_among_safe(self, filt):
        with pytest.raises(SecurityError, match="DeleteFile"):
            filt.check("result = DeleteFile[\"/tmp/x\"]")

    def test_dynamic_symbol_blocked(self, filt):
        with pytest.raises(SecurityError, match="Run"):
            filt.check('Symbol["Run"]')

    def test_get_operator_not_blocked_if_not_in_policy(self, filt):
        # "Get" is not in the blocked set, so << should pass
        filt.check('<< "package.wl"')

    def test_get_operator_blocked_if_in_policy(self):
        filt = ExpressionFilter("blacklist", frozenset({"Get"}))
        with pytest.raises(SecurityError, match="Get"):
            filt.check('<< "package.wl"')

    def test_context_qualified_blocked(self, filt):
        with pytest.raises(SecurityError, match="Run"):
            filt.check("System`Run[\"cmd\"]")

    def test_string_contents_not_blocked(self, filt):
        # "Run" only appears inside a string literal
        filt.check('"Run is not a function call"')


# ===================================================================
# ExpressionFilter — whitelist mode
# ===================================================================

class TestWhitelistFilter:
    """Tests for whitelist-mode ExpressionFilter."""

    @pytest.fixture
    def filt(self):
        allowed = frozenset({
            "Sin", "Cos", "Plus", "Times", "Power",
            "x", "y", "Pi", "E", "Integrate",
        })
        return ExpressionFilter("whitelist", allowed)

    def test_allowed_expression_passes(self, filt):
        filt.check("Sin[x] + Cos[y]")

    def test_unknown_symbol_raises(self, filt):
        with pytest.raises(SecurityError, match="Run"):
            filt.check("Run[\"ls\"]")

    def test_all_symbols_must_be_allowed(self, filt):
        with pytest.raises(SecurityError):
            filt.check("BesselJ[0, x]")  # BesselJ not in whitelist

    def test_empty_expression_passes(self, filt):
        filt.check("")

    def test_number_only_passes(self, filt):
        filt.check("42")


# ===================================================================
# CapabilityRegistry
# ===================================================================

class TestCapabilityRegistry:
    """Tests for group loading and filter building."""

    @pytest.fixture
    def registry(self):
        return CapabilityRegistry()

    def test_groups_loaded(self, registry):
        groups = registry.available_groups()
        assert "math_core" in groups
        assert "system_exec" in groups

    def test_build_blacklist_filter(self, registry):
        config = SecurityConfig(
            mode="blacklist",
            deny_groups=["system_exec"],
        )
        filt = registry.build_filter(config)
        assert filt.mode == "blacklist"
        # Run should be blocked (it's in system_exec)
        with pytest.raises(SecurityError):
            filt.check("Run[\"ls\"]")
        # Sin should pass
        filt.check("Sin[x]")

    def test_build_whitelist_filter(self, registry):
        config = SecurityConfig(
            mode="whitelist",
            allow_groups=["math_core"],
        )
        filt = registry.build_filter(config)
        assert filt.mode == "whitelist"
        # Plus is in math_core
        filt.check("1 + 2")

    def test_extra_blocked(self, registry):
        config = SecurityConfig(
            mode="blacklist",
            deny_groups=["system_exec"],
            extra_blocked=["MyDangerousFunc"],
        )
        filt = registry.build_filter(config)
        with pytest.raises(SecurityError, match="MyDangerousFunc"):
            filt.check("MyDangerousFunc[x]")

    def test_default_blacklist_blocks_dangerous(self, registry):
        """Default SecurityConfig should block dangerous symbols."""
        config = SecurityConfig()  # defaults: blacklist + all 6 dangerous groups
        filt = registry.build_filter(config)
        for sym in ("Run", "RunProcess", "DeleteFile", "URLRead", "ToExpression"):
            with pytest.raises(SecurityError):
                filt.check(f"{sym}[x]")

    def test_default_blacklist_allows_math(self, registry):
        """Default SecurityConfig should allow standard math."""
        config = SecurityConfig()
        filt = registry.build_filter(config)
        filt.check("Integrate[Sin[x], {x, 0, Pi}]")
        filt.check("Solve[x^2 - 1 == 0, x]")
        filt.check("Plot[Sin[x], {x, 0, 2 Pi}]")

    def test_whitelist_restricts_to_configured_groups(self, registry):
        """whitelist=["math_core"] must reject symbols from other groups
        (e.g. Plot from visualization, Solve from algebra).

        Regression test: whitelist must not silently widen beyond
        the configured allow_groups.
        """
        config = SecurityConfig(mode="whitelist", allow_groups=["math_core"])
        filt = registry.build_filter(config)

        # Sin[Pi] should pass (both in math_core group)
        filt.check("Sin[Pi]")

        # Plot is NOT in math_core — must be rejected
        with pytest.raises(SecurityError):
            filt.check("Plot[Sin[x], {x, 0, 1}]")

        # Solve is NOT in math_core — must be rejected
        with pytest.raises(SecurityError):
            filt.check("Solve[x^2 == 1, x]")
