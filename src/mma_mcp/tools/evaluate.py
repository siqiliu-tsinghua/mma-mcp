"""Core evaluation tools: evaluate and evaluate_image."""

from __future__ import annotations

from mcp.server.fastmcp import Image

from mma_mcp.security.filter import SecurityError
from mma_mcp.tools import ToolContext, register

_ALLOWED_FORMS = frozenset({
    "TeXForm", "OutputForm", "InputForm", "StandardForm", "TraditionalForm",
})


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
    if fmt not in _ALLOWED_FORMS:
        raise SecurityError(
            f"Invalid output form {fmt!r}. "
            f"Allowed: {', '.join(sorted(_ALLOWED_FORMS))}"
        )
    with ctx.pool.worker() as (kernel, wl_context):
        result = kernel.evaluate_to_string(
            expression, fmt,
            timeout=ctx.timeout, hard_timeout=ctx.hard_timeout,
            context=wl_context,
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
    with ctx.pool.worker() as (kernel, wl_context):
        png_bytes = kernel.evaluate_to_image(
            expression,
            timeout=ctx.timeout, hard_timeout=ctx.hard_timeout,
            context=wl_context,
        )
    return Image(data=png_bytes, format="png")
