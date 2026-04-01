"""Structured plotting tool — semantic interface for Wolfram Language plots."""

from __future__ import annotations

from mcp.server.fastmcp import Image

from mma_mcp.tools import ToolContext, register

# Supported plot types → WL function name
_PLOT_TYPES = {
    "plot": "Plot",
    "listplot": "ListPlot",
    "listlineplot": "ListLinePlot",
    "parametricplot": "ParametricPlot",
    "polarplot": "PolarPlot",
    "logplot": "LogPlot",
    "loglogplot": "LogLogPlot",
    "plot3d": "Plot3D",
    "contourplot": "ContourPlot",
    "densityplot": "DensityPlot",
    "streamplot": "StreamPlot",
    "vectorplot": "VectorPlot",
    "listplot3d": "ListPlot3D",
    "listcontourplot": "ListContourPlot",
}


@register("plot")
def plot(
    ctx: ToolContext,
    expression: str,
    variable: str,
    range_min: str = "",
    range_max: str = "",
    variable2: str = "",
    range2_min: str = "",
    range2_max: str = "",
    plot_type: str = "plot",
    options: str = "",
) -> Image:
    """Create a plot and return it as a PNG image.

    Supports 1D plots (Plot, ListPlot, PolarPlot, LogPlot, …) and
    2D plots (Plot3D, ContourPlot, DensityPlot, …).

    Args:
        expression:  The expression(s) to plot, e.g. "Sin[x]", "{Sin[x], Cos[x]}",
                     or a list of data points "{1,4,9,16,25}".
        variable:    Primary variable, e.g. "x".
        range_min:   Lower bound of primary range, e.g. "0".
        range_max:   Upper bound of primary range, e.g. "2 Pi".
        variable2:   Secondary variable for 3D plots, e.g. "y".
        range2_min:  Lower bound of secondary range.
        range2_max:  Upper bound of secondary range.
        plot_type:   One of: plot, listplot, listlineplot, parametricplot,
                     polarplot, logplot, loglogplot, plot3d, contourplot,
                     densityplot, streamplot, vectorplot, listplot3d,
                     listcontourplot. Default: "plot".
        options:     Additional WL plot options, e.g.
                     "PlotStyle -> Red, PlotLabel -> \\"My Plot\\"".
    """
    key = plot_type.lower().replace("_", "")
    wl_func = _PLOT_TYPES.get(key)
    if wl_func is None:
        supported = ", ".join(sorted(_PLOT_TYPES.keys()))
        raise ValueError(
            f"Unknown plot_type '{plot_type}'. Supported: {supported}"
        )

    # Build the range specification
    if range_min and range_max:
        range1 = f"{{{variable}, {range_min}, {range_max}}}"
    else:
        range1 = variable

    # Build full expression
    if variable2 and range2_min and range2_max:
        range2 = f"{{{variable2}, {range2_min}, {range2_max}}}"
        args = f"{expression}, {range1}, {range2}"
    else:
        args = f"{expression}, {range1}"

    if options:
        args += f", {options}"

    expr = f"{wl_func}[{args}]"

    # Security check on the full expression
    ctx.check(expr)

    png_bytes = ctx.kernel.evaluate_to_image(
        expr, timeout=ctx.timeout, hard_timeout=ctx.hard_timeout, context=ctx.session_context,
    )
    return Image(data=png_bytes, format="png")
