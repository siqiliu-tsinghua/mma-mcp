"""Mathematical convenience tools: solve, simplify, integrate, differentiate."""

from __future__ import annotations

from mma_mcp.tools import ToolContext, register


@register("solve")
def solve(ctx: ToolContext, equations: str, variables: str, numeric: bool = False) -> str:
    """Solve one or more equations for specified variables.

    Args:
        equations: A WL equation or list, e.g. "x^2 - 1 == 0" or "{x+y==1, x-y==3}".
        variables: Variable or list, e.g. "x" or "{x, y}".
        numeric:   Use NSolve for numerical solutions.
    """
    ctx.check(equations, variables)
    fn = "NSolve" if numeric else "Solve"
    result = ctx.kernel.evaluate_to_string(
        f"{fn}[{equations}, {variables}]",
        ctx.default_format,
        timeout=ctx.timeout, hard_timeout=ctx.hard_timeout, context=ctx.session_context,
    )
    return ctx.truncate(result)


@register("simplify")
def simplify(ctx: ToolContext, expression: str, full: bool = False, assumptions: str = "") -> str:
    """Simplify a mathematical expression.

    Args:
        expression:  WL expression to simplify.
        full:        Use FullSimplify (slower but more thorough).
        assumptions: Optional WL assumption, e.g. "x > 0".
    """
    ctx.check(expression)
    if assumptions:
        ctx.check(assumptions)
    fn = "FullSimplify" if full else "Simplify"
    if assumptions:
        expr = f"{fn}[{expression}, {assumptions}]"
    else:
        expr = f"{fn}[{expression}]"
    result = ctx.kernel.evaluate_to_string(
        expr, ctx.default_format, timeout=ctx.timeout, hard_timeout=ctx.hard_timeout, context=ctx.session_context,
    )
    return ctx.truncate(result)


@register("integrate")
def integrate(
    ctx: ToolContext,
    expression: str,
    variable: str,
    lower: str = "",
    upper: str = "",
    numeric: bool = False,
) -> str:
    """Compute a definite or indefinite integral.

    Args:
        expression: WL integrand.
        variable:   Integration variable, e.g. "x".
        lower:      Lower bound (omit for indefinite integral).
        upper:      Upper bound (omit for indefinite integral).
        numeric:    Use NIntegrate for numerical evaluation.
    """
    ctx.check(expression, variable)
    if lower and upper:
        ctx.check(lower, upper)
    fn = "NIntegrate" if numeric else "Integrate"
    if lower and upper:
        expr = f"{fn}[{expression}, {{{variable}, {lower}, {upper}}}]"
    else:
        expr = f"{fn}[{expression}, {variable}]"
    result = ctx.kernel.evaluate_to_string(
        expr, ctx.default_format, timeout=ctx.timeout, hard_timeout=ctx.hard_timeout, context=ctx.session_context,
    )
    return ctx.truncate(result)


@register("differentiate")
def differentiate(ctx: ToolContext, expression: str, variable: str, order: int = 1) -> str:
    """Differentiate an expression with respect to a variable.

    Args:
        expression: WL expression to differentiate.
        variable:   Differentiation variable, e.g. "x".
        order:      Order of the derivative (default 1).
    """
    ctx.check(expression, variable)
    if order == 1:
        expr = f"D[{expression}, {variable}]"
    else:
        expr = f"D[{expression}, {{{variable}, {order}}}]"
    result = ctx.kernel.evaluate_to_string(
        expr, ctx.default_format, timeout=ctx.timeout, hard_timeout=ctx.hard_timeout, context=ctx.session_context,
    )
    return ctx.truncate(result)
