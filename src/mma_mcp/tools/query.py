"""WolframAlpha-style natural language query tool.

Requires the ``external_services`` security group to be enabled, as it
uses the WolframAlpha[] function which makes network calls to Wolfram servers.
"""

from __future__ import annotations

from mma_mcp.tools import ToolContext, register


@register("query")
def query(ctx: ToolContext, input: str) -> str:
    """Query Wolfram|Alpha with a natural language input.

    This tool sends the input to Wolfram|Alpha via the kernel's built-in
    WolframAlpha[] function and returns a human-readable result.

    NOTE: Requires internet access and the ``external_services`` security
    group to be enabled in your configuration.

    Args:
        input:  Natural language query, e.g. "population of France",
                "integrate sin(x) from 0 to pi", "weather in Beijing".
    """
    # Security check covers WolframAlpha symbol
    expr = f'WolframAlpha["{_escape_wl_string(input)}", "Result"]'
    ctx.check(expr)
    result = ctx.kernel.evaluate_to_string(
        expr, "OutputForm", timeout=ctx.timeout, hard_timeout=ctx.hard_timeout,
        context=ctx.session_context,
    )
    return ctx.truncate(result)


def _escape_wl_string(s: str) -> str:
    """Escape a string for use inside WL double quotes."""
    return s.replace("\\", "\\\\").replace('"', '\\"')
