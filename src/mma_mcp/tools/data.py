"""Curated data query tool — access Wolfram's built-in knowledge base.

Most data functions (CountryData, ElementData, etc.) are bundled with
Wolfram Engine and work offline.  A few (FinancialData, WeatherData)
fetch live data from the internet and require the ``external_services``
security group to be enabled.
"""

from __future__ import annotations

from mma_mcp.tools import ToolContext, register

# Supported data sources → WL function name
_DATA_SOURCES = {
    "country": "CountryData",
    "city": "CityData",
    "element": "ElementData",
    "chemical": "ChemicalData",
    "planet": "PlanetData",
    "star": "StarData",
    "unit": "UnitConvert",
    "movie": "MovieData",
    "word": "WordData",
    "genome": "GenomeData",
    "polyhedron": "PolyhedronData",
    "knot": "KnotData",
    "graph": "GraphData",
    "isotope": "IsotopeData",
    "mineral": "MineralData",
    "satellite": "SatelliteData",
    "aircraft": "AircraftData",
    "food": "FoodData",
    # These fetch live data from the internet — require external_services group
    "financial": "FinancialData",
    "weather": "WeatherData",
}


@register("data_query")
def data_query(
    ctx: ToolContext,
    source: str,
    entity: str,
    property: str = "",
) -> str:
    """Query Wolfram's built-in curated data.

    Most data sources are bundled locally with Wolfram Engine and work
    offline. Two sources fetch live data from the internet and require
    the ``external_services`` security group: ``financial``, ``weather``.

    Args:
        source:   Data source — one of: country, city, element, chemical,
                  planet, star, unit, movie, word, genome, polyhedron,
                  knot, graph, isotope, mineral, satellite, aircraft, food,
                  financial (live), weather (live).
        entity:   The entity to query, e.g. "France", "Gold", "Mars".
        property: Optional property to retrieve, e.g. "Population",
                  "AtomicNumber", "Radius". Omit to get a summary or
                  default property.
    """
    key = source.lower()
    wl_func = _DATA_SOURCES.get(key)
    if wl_func is None:
        supported = ", ".join(sorted(_DATA_SOURCES.keys()))
        raise ValueError(
            f"Unknown data source '{source}'. Supported: {supported}"
        )

    entity_escaped = _escape_wl_string(entity)

    if property:
        prop_escaped = _escape_wl_string(property)
        expr = f'{wl_func}["{entity_escaped}", "{prop_escaped}"]'
    else:
        expr = f'{wl_func}["{entity_escaped}"]'

    ctx.check(expr)
    result = ctx.kernel.evaluate_to_string(
        expr, ctx.default_format, timeout=ctx.timeout, hard_timeout=ctx.hard_timeout,
        context=ctx.session_context,
    )
    return ctx.truncate(result)


def _escape_wl_string(s: str) -> str:
    """Escape a string for use inside WL double quotes."""
    return s.replace("\\", "\\\\").replace('"', '\\"')
