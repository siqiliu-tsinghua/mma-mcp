"""Core evaluation tools: evaluate and evaluate_image."""

from __future__ import annotations

import asyncio

from mcp.server.fastmcp import Context, Image

import logging

from mma_mcp.security.filter import SecurityError
from mma_mcp.tools import ToolContext, register

logger = logging.getLogger(__name__)

_ALLOWED_FORMS = frozenset({
    "TeXForm", "OutputForm", "InputForm", "StandardForm", "TraditionalForm",
})

_HEARTBEAT_INTERVAL = 5  # seconds between progress notifications


async def _run_with_heartbeat(
    func, mcp_ctx: Context | None, hard_timeout: int,
) -> object:
    """Run a blocking *func* in a thread, sending progress heartbeats.

    *func* is a zero-arg callable executed via ``run_in_executor``.
    While it runs, ``report_progress()`` is called every few seconds so the
    MCP client knows the server is still alive.
    """
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(None, func)

    elapsed = 0
    while not future.done():
        try:
            result = await asyncio.wait_for(
                asyncio.shield(future), timeout=_HEARTBEAT_INTERVAL,
            )
            return result
        except asyncio.TimeoutError:
            elapsed += _HEARTBEAT_INTERVAL
            if mcp_ctx:
                logger.info("Heartbeat: %ds elapsed", elapsed)
                await mcp_ctx.report_progress(
                    elapsed, hard_timeout or None,
                    f"Computing… ({elapsed}s)",
                )
            else:
                logger.debug("Heartbeat: %ds elapsed (no progressToken)", elapsed)

    return future.result()


@register("evaluate")
async def evaluate(
    ctx: ToolContext, expression: str, form: str = "",
    mcp_ctx: Context | None = None,
) -> str:
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

    def _do_eval() -> str:
        with ctx.pool.worker() as (kernel, wl_context):
            return kernel.evaluate_to_string(
                expression, fmt,
                timeout=ctx.timeout, hard_timeout=ctx.hard_timeout,
                context=wl_context,
            )

    result = await _run_with_heartbeat(_do_eval, mcp_ctx, ctx.hard_timeout)
    return ctx.truncate(result)


@register("evaluate_image")
async def evaluate_image(
    ctx: ToolContext, expression: str,
    mcp_ctx: Context | None = None,
) -> Image:
    """Evaluate a Wolfram Language expression and return the result as a PNG image.

    Useful for Plot, Graphics, or any expression with visual output.

    Args:
        expression: A valid Wolfram Language expression string.
    """
    ctx.check(expression)

    def _do_eval() -> bytes:
        with ctx.pool.worker() as (kernel, wl_context):
            return kernel.evaluate_to_image(
                expression,
                timeout=ctx.timeout, hard_timeout=ctx.hard_timeout,
                context=wl_context,
            )

    png_bytes = await _run_with_heartbeat(_do_eval, mcp_ctx, ctx.hard_timeout)
    return Image(data=png_bytes, format="png")
