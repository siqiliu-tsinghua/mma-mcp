"""Integration tests — real Wolfram kernel, full pipeline.

These tests require a working Wolfram Engine installation.
Skip with: pytest -m "not integration"
"""

from __future__ import annotations

import pytest

from mma_mcp.config import AppConfig, KernelConfig, SecurityConfig
from mma_mcp.kernel import KernelSession, _eval_in_context
from mma_mcp.security.filter import ExpressionFilter, SecurityError
from mma_mcp.security.registry import CapabilityRegistry

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration



# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture(scope="module")
def kernel():
    """Start a real kernel session, shared across all tests in this module."""
    ks = KernelSession()
    ks.start()
    yield ks
    ks.stop()


@pytest.fixture(scope="module")
def registry():
    return CapabilityRegistry()


# ===================================================================
# 1. Kernel basic evaluation
# ===================================================================

class TestKernelBasic:

    def test_simple_arithmetic(self, kernel):
        result = kernel.evaluate_to_string("1 + 1", "OutputForm")
        assert result.strip() == "2"

    def test_symbolic_expression(self, kernel):
        result = kernel.evaluate_to_string("Expand[(x+1)^3]", "OutputForm")
        # OutputForm may render as "1 + 3 x + 3 x^2 + x^3" or similar
        assert "3" in result and "x" in result

    def test_tex_form(self, kernel):
        result = kernel.evaluate_to_string("Sqrt[x^2 + 1]", "TeXForm")
        assert "sqrt" in result.lower() or "\\sqrt" in result

    def test_numeric_evaluation(self, kernel):
        result = kernel.evaluate_to_string("N[Pi, 20]", "OutputForm")
        assert result.startswith("3.14159")

    def test_list_operations(self, kernel):
        result = kernel.evaluate_to_string("Sort[{3,1,4,1,5,9}]", "OutputForm")
        assert "1" in result and "9" in result

    def test_timeout_constrained(self, kernel):
        """TimeConstrained should abort long computations."""
        result = kernel.evaluate_to_string(
            "While[True]", "OutputForm", timeout=2,
        )
        assert "$Aborted" in result


    def test_image_output(self, kernel):
        """evaluate_to_image should return valid PNG bytes."""
        png = kernel.evaluate_to_image("Plot[Sin[x], {x, 0, 2 Pi}]", timeout=15)
        assert png[:4] == b"\x89PNG"
        assert len(png) > 1000  # a real plot should be at least a few KB


# ===================================================================
# 2. Security filter + kernel pipeline
# ===================================================================

class TestSecurityPipeline:

    def test_blacklist_blocks_run(self, kernel, registry):
        config = SecurityConfig(mode="blacklist")
        filt = registry.build_filter(config)
        with pytest.raises(SecurityError, match="Run"):
            filt.check('Run["ls"]')

    def test_blacklist_allows_math(self, kernel, registry):
        config = SecurityConfig(mode="blacklist")
        filt = registry.build_filter(config)
        filt.check("Integrate[Sin[x], x]")  # should not raise
        result = kernel.evaluate_to_string("Integrate[Sin[x], x]", "OutputForm")
        assert "Cos" in result or "cos" in result

    def test_whitelist_allows_configured_groups(self, kernel, registry):
        config = SecurityConfig(
            mode="whitelist",
            allow_groups=["math_core", "algebra", "calculus"],
            extra_allowed=["x", "y"],  # user variables need explicit allow in whitelist
        )
        filt = registry.build_filter(config)
        filt.check("Solve[x^2 - 1 == 0, x]")  # should pass
        result = kernel.evaluate_to_string("Solve[x^2 - 1 == 0, x]", "OutputForm")
        assert "1" in result and "-1" in result

    def test_whitelist_blocks_plotting(self, kernel, registry):
        config = SecurityConfig(
            mode="whitelist",
            allow_groups=["math_core"],  # no plotting
        )
        filt = registry.build_filter(config)
        with pytest.raises(SecurityError):
            filt.check("Plot[Sin[x], {x, 0, 2 Pi}]")

    def test_extra_blocked_symbol(self, kernel, registry):
        config = SecurityConfig(
            mode="blacklist",
            extra_blocked=["FactorInteger"],
        )
        filt = registry.build_filter(config)
        with pytest.raises(SecurityError, match="FactorInteger"):
            filt.check("FactorInteger[100]")


# ===================================================================
# 3. Worker pool context isolation
# ===================================================================

class TestPoolContextIsolation:

    def test_temp_context_isolates_variables(self, kernel):
        """Variables set in a temp context should not leak to another."""
        ctx_a = "Pool$aaa`"
        ctx_b = "Pool$bbb`"

        # Set variable in context A
        kernel.evaluate_to_string("myVar = 42", "OutputForm", context=ctx_a)

        # Context B should NOT see it
        result_bob = kernel.evaluate_to_string("myVar", "OutputForm", context=ctx_b)
        assert "42" not in result_bob

        # Context A should still see it
        result_alice = kernel.evaluate_to_string("myVar", "OutputForm", context=ctx_a)
        assert result_alice.strip() == "42"

        # Cleanup (simulates pool release)
        from wolframclient.language import wlexpr
        kernel.evaluate(wlexpr(f'Remove["{ctx_a}*"]'))
        kernel.evaluate(wlexpr(f'Remove["{ctx_b}*"]'))

    def test_system_symbols_accessible_in_temp_context(self, kernel):
        """System` functions should work inside a pool temp context."""
        result = kernel.evaluate_to_string(
            "Sin[Pi/2]", "OutputForm", context="Pool$test`",
        )
        assert result.strip() == "1"

    def test_cleanup_removes_temp_symbols(self, kernel):
        """Remove[ctx*] should clean up all symbols from the temp context."""
        ctx = "Pool$cleanup`"
        from wolframclient.language import wlexpr

        kernel.evaluate_to_string("testCleanupVar = 123", "OutputForm", context=ctx)
        result = kernel.evaluate_to_string("testCleanupVar", "OutputForm", context=ctx)
        assert result.strip() == "123"

        # Simulate pool cleanup
        kernel.evaluate(wlexpr(f'Remove["{ctx}*"]'))

        # Variable should be gone
        result2 = kernel.evaluate_to_string("testCleanupVar", "OutputForm", context=ctx)
        assert "123" not in result2


# ===================================================================
# 4. Security filter + kernel pipeline (various domains)
# ===================================================================

class TestFilterKernelPipeline:

    def test_solve(self, kernel, registry):
        """Solve passes security filter and returns correct result."""
        config = SecurityConfig(mode="blacklist")
        filt = registry.build_filter(config)
        expr = "Solve[x^2 - 5 x + 6 == 0, x]"
        filt.check(expr)
        result = kernel.evaluate_to_string(expr, "TeXForm")
        assert "2" in result and "3" in result

    def test_integrate(self, kernel, registry):
        config = SecurityConfig(mode="blacklist")
        filt = registry.build_filter(config)
        expr = "Integrate[x^2, x]"
        filt.check(expr)
        result = kernel.evaluate_to_string(expr, "TeXForm")
        assert "x^3" in result or "frac" in result

    def test_differentiate(self, kernel, registry):
        config = SecurityConfig(mode="blacklist")
        filt = registry.build_filter(config)
        expr = "D[Sin[x] Cos[x], x]"
        filt.check(expr)
        result = kernel.evaluate_to_string(expr, "OutputForm")
        assert "Cos" in result or "Sin" in result


    def test_plot_to_image(self, kernel, registry):
        config = SecurityConfig(mode="blacklist")
        filt = registry.build_filter(config)
        expr = "Plot[{Sin[x], Cos[x]}, {x, 0, 2 Pi}]"
        filt.check(expr)
        png = kernel.evaluate_to_image(expr, timeout=15)
        assert png[:4] == b"\x89PNG"

    def test_simplify_with_assumptions(self, kernel, registry):
        config = SecurityConfig(mode="blacklist")
        filt = registry.build_filter(config)
        expr = "Simplify[Sqrt[x^2], x > 0]"
        filt.check(expr)
        result = kernel.evaluate_to_string(expr, "OutputForm")
        assert result.strip() == "x"
