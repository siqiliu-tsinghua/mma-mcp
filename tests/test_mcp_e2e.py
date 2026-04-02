"""End-to-end tests via MCP protocol (FastMCP.call_tool).

These tests create a full App → FastMCP server → call_tool pipeline,
exercising the real Wolfram kernel through the MCP tool interface.

Skip with: pytest -m "not integration"
"""

from __future__ import annotations

import os
import shutil

import pytest

from mma_mcp.config import AppConfig, KernelConfig, SecurityConfig, ToolsConfig
from mma_mcp.server import App

pytestmark = pytest.mark.integration

_has_display = bool(os.environ.get("DISPLAY")) or shutil.which("Xvfb") is not None
needs_display = pytest.mark.skipif(
    not _has_display, reason="No DISPLAY or Xvfb available for graphics rendering",
)


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture(scope="module")
def app():
    """Create an App with default config (blacklist mode, all safe tools)."""
    config = AppConfig(
        kernel=KernelConfig(timeout=15, hard_timeout=30),
        security=SecurityConfig(mode="blacklist"),
        tools=ToolsConfig(enabled=[
            "evaluate", "evaluate_image", "solve", "simplify",
            "integrate", "differentiate", "plot", "data_query",
        ]),
    )
    a = App(config=config)
    # Force kernel start so all tests share one session
    a.kernel.start()
    yield a
    a.kernel.stop()


@pytest.fixture(scope="module")
def mcp(app):
    """Return the configured FastMCP server."""
    return app.mcp


# ===================================================================
# Helper
# ===================================================================

def get_text(result) -> str:
    """Extract text from MCP call_tool result."""
    from mcp.types import TextContent
    for block in result:
        if isinstance(block, TextContent):
            return block.text
    raise AssertionError(f"No TextContent in result: {result}")


def get_image_data(result) -> bytes:
    """Extract image bytes from MCP call_tool result."""
    import base64
    from mcp.types import ImageContent
    for block in result:
        if isinstance(block, ImageContent):
            return base64.b64decode(block.data)
    raise AssertionError(f"No ImageContent in result: {result}")


# ===================================================================
# Tests: evaluate tool
# ===================================================================

class TestEvaluateTool:

    @pytest.mark.asyncio
    async def test_simple_expression(self, mcp):
        result = await mcp.call_tool("evaluate", {"expression": "1 + 1"})
        text = get_text(result)
        assert "2" in text

    @pytest.mark.asyncio
    async def test_symbolic_math(self, mcp):
        result = await mcp.call_tool("evaluate", {
            "expression": "Expand[(x+1)^3]",
        })
        text = get_text(result)
        assert "x" in text

    @pytest.mark.asyncio
    async def test_tex_form(self, mcp):
        result = await mcp.call_tool("evaluate", {
            "expression": "Sqrt[x^2 + 1]",
            "form": "TeXForm",
        })
        text = get_text(result)
        assert "sqrt" in text.lower() or "\\sqrt" in text

    @pytest.mark.asyncio
    async def test_output_form(self, mcp):
        result = await mcp.call_tool("evaluate", {
            "expression": "N[Pi, 10]",
            "form": "OutputForm",
        })
        text = get_text(result)
        assert text.strip().startswith("3.14159")

    @pytest.mark.asyncio
    async def test_security_blocks_dangerous(self, mcp):
        """Dangerous expressions should be caught by the security filter."""
        result = await mcp.call_tool("evaluate", {
            "expression": 'Run["echo pwned"]',
        })
        text = get_text(result)
        assert "Security Error" in text

    @pytest.mark.asyncio
    async def test_timeout_returns_aborted(self, mcp):
        """Long computation should return $Aborted."""
        result = await mcp.call_tool("evaluate", {
            "expression": "While[True]",
        })
        text = get_text(result)
        assert "$Aborted" in text or "Timeout" in text


# ===================================================================
# Tests: math tools
# ===================================================================

class TestMathTools:

    @pytest.mark.asyncio
    async def test_solve(self, mcp):
        result = await mcp.call_tool("solve", {
            "equations": "x^2 - 5x + 6 == 0",
            "variables": "x",
        })
        text = get_text(result)
        assert "2" in text and "3" in text

    @pytest.mark.asyncio
    async def test_integrate(self, mcp):
        result = await mcp.call_tool("integrate", {
            "expression": "x^2",
            "variable": "x",
        })
        text = get_text(result)
        assert "x" in text  # should contain x^3/3

    @pytest.mark.asyncio
    async def test_differentiate(self, mcp):
        result = await mcp.call_tool("differentiate", {
            "expression": "Sin[x] Cos[x]",
            "variable": "x",
        })
        text = get_text(result)
        assert "Cos" in text or "Sin" in text or "cos" in text or "sin" in text

    @pytest.mark.asyncio
    async def test_simplify(self, mcp):
        result = await mcp.call_tool("simplify", {
            "expression": "Sin[x]^2 + Cos[x]^2",
        })
        text = get_text(result)
        assert "1" in text


# ===================================================================
# Tests: image tools
# ===================================================================

class TestImageTools:

    @needs_display
    @pytest.mark.asyncio
    async def test_evaluate_image(self, mcp):
        result = await mcp.call_tool("evaluate_image", {
            "expression": "Plot[Sin[x], {x, 0, 2 Pi}]",
        })
        png = get_image_data(result)
        assert png[:4] == b"\x89PNG"
        assert len(png) > 1000

    @needs_display
    @pytest.mark.asyncio
    async def test_plot_tool(self, mcp):
        result = await mcp.call_tool("plot", {
            "expression": "Sin[x]",
            "variable": "x",
            "range_min": "0",
            "range_max": "2 Pi",
            "plot_type": "plot",
        })
        png = get_image_data(result)
        assert png[:4] == b"\x89PNG"

    @needs_display
    @pytest.mark.asyncio
    async def test_plot_3d(self, mcp):
        result = await mcp.call_tool("plot", {
            "expression": "Sin[x] Cos[y]",
            "variable": "x",
            "range_min": "-Pi",
            "range_max": "Pi",
            "variable2": "y",
            "range2_min": "-Pi",
            "range2_max": "Pi",
            "plot_type": "plot3d",
        })
        png = get_image_data(result)
        assert png[:4] == b"\x89PNG"


# ===================================================================
# Tests: data_query tool
# ===================================================================

class TestDataQueryTool:

    @pytest.mark.asyncio
    async def test_country_data(self, mcp):
        result = await mcp.call_tool("data_query", {
            "source": "country",
            "entity": "France",
            "property": "Population",
        })
        text = get_text(result)
        assert len(text.strip()) > 0

    @pytest.mark.asyncio
    async def test_element_data(self, mcp):
        result = await mcp.call_tool("data_query", {
            "source": "element",
            "entity": "Gold",
            "property": "AtomicNumber",
        })
        text = get_text(result)
        assert "79" in text


# ===================================================================
# Tests: error handling
# ===================================================================

class TestErrorHandling:

    @pytest.mark.asyncio
    async def test_kernel_error_returns_message(self, mcp):
        """Malformed expression should return an error, not crash the server."""
        result = await mcp.call_tool("evaluate", {
            "expression": "Plot[",
        })
        text = get_text(result)
        # Should get some result (possibly error from WL), not an exception
        assert isinstance(text, str)

    @pytest.mark.asyncio
    async def test_unknown_tool_raises(self, mcp):
        """Calling a non-existent tool should raise."""
        with pytest.raises(Exception):
            await mcp.call_tool("nonexistent_tool", {})
