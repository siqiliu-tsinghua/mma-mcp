"""Core evaluation tools: evaluate and evaluate_image."""

from __future__ import annotations

from mcp.server.fastmcp import Image

from mma_mcp.tools import ToolContext, register


@register("evaluate")
def evaluate(ctx: ToolContext, expression: str, form: str = "") -> str:
    """Evaluate a Wolfram Language expression and return the result as text.

    Args:
        expression: A valid Wolfram Language expression string.
        form:       Output format — TeXForm (default), OutputForm, InputForm,
                    StandardForm, or TraditionalForm.
    """
    ctx.check(expression)
    fmt = form or ctx.default_format
    result = ctx.kernel.evaluate_to_string(
        expression, fmt, timeout=ctx.timeout, hard_timeout=ctx.hard_timeout,
    )
    return ctx.truncate(result)


@register("evaluate_image")
def evaluate_image(ctx: ToolContext, expression: str) -> Image:
    """Evaluate a Wolfram Language expression and return the result as a PNG image.

    Useful for Plot, Graphics, or any expression with visual output.

    Args:
        expression: A valid Wolfram Language expression string.
    """
    ctx.check(expression)
    png_bytes = ctx.kernel.evaluate_to_image(
        expression, timeout=ctx.timeout, hard_timeout=ctx.hard_timeout,
    )
    return Image(data=png_bytes, format="png")
