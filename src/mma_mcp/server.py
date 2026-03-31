"""MCP server entry point for mma-mcp (FastMCP-based).

Start with:
    uv run mma-mcp
or for development/inspection:
    uv run mcp dev src/mma_mcp/server.py

Configuration is read from [tool.mma-mcp] in pyproject.toml,
or from mma_mcp.toml in the current working directory.
Kernel path can also be set via WOLFRAM_KERNEL env var.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP, Image

from mma_mcp.kernel import KernelSession
from mma_mcp.security.registry import CapabilityRegistry, SecurityConfig
from mma_mcp.stdio_transport import stdio_transport

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_config() -> dict[str, Any]:
    for candidate in ("mma_mcp.toml", "pyproject.toml"):
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            if sys.version_info >= (3, 11):
                import tomllib
            else:
                import tomli as tomllib  # type: ignore[no-redef]
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            if candidate == "pyproject.toml":
                return data.get("tool", {}).get("mma-mcp", {})
            return data
        except Exception:
            logger.warning("Failed to parse %s", candidate, exc_info=True)
    return {}


def _build_security_config(cfg: dict[str, Any]) -> SecurityConfig:
    sec = cfg.get("security", {})
    return SecurityConfig(
        mode=sec.get("mode", "blacklist"),
        groups=sec.get("groups", []),
        extra_blocked=sec.get("extra_blocked", []),
        extra_allowed=sec.get("extra_allowed", []),
    )


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

_kernel: KernelSession | None = None

# Security filter is built at startup from config only (no kernel needed).
# Whitelist mode can optionally be refined with live system symbols after
# kernel starts, but the filter is always valid from server boot onwards.
_config = _load_config()
_registry = CapabilityRegistry()
_expr_filter = _registry.build_filter(_build_security_config(_config))
logger.info("Security filter ready (mode: %s)", _build_security_config(_config).mode)


def _setup() -> KernelSession:
    global _kernel
    if _kernel is not None:
        return _kernel

    kernel_path: str | None = _config.get("kernel") or os.environ.get("WOLFRAM_KERNEL")
    kernel_obj = KernelSession(kernel=kernel_path)
    kernel_obj.start()  # raises WolframKernelException on failure; _kernel unchanged
    _kernel = kernel_obj

    # Refine whitelist with live system symbols if applicable
    sec_cfg = _build_security_config(_config)
    if sec_cfg.mode == "whitelist":
        try:
            _registry.initialize_system_symbols(_kernel.get_all_system_symbols())
            global _expr_filter
            _expr_filter = _registry.build_filter(sec_cfg)
        except Exception:
            logger.warning("Could not fetch system symbols; whitelist uses group files only")

    logger.info("Kernel ready")
    return _kernel


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

mcp = FastMCP("mma-mcp")


@mcp.tool()
def evaluate(expression: str, form: str = "OutputForm") -> str:
    """Evaluate a Wolfram Language expression and return the result as text.

    Args:
        expression: A valid Wolfram Language expression string.
        form:       Output format — OutputForm (default), TeXForm, InputForm,
                    StandardForm, or TraditionalForm.
    """
    _expr_filter.check(expression)
    kernel = _setup()
    return kernel.evaluate_to_string(expression, form)


@mcp.tool()
def evaluate_image(expression: str) -> Image:
    """Evaluate a Wolfram Language expression and return the result as a PNG image.

    Useful for Plot, Graphics, or any expression with visual output.

    Args:
        expression: A valid Wolfram Language expression string.
    """
    _expr_filter.check(expression)
    kernel = _setup()
    png_bytes = kernel.evaluate_to_image(expression)
    return Image(data=png_bytes, format="png")


@mcp.tool()
def solve(equations: str, variables: str, numeric: bool = False) -> str:
    """Solve one or more equations for specified variables.

    Args:
        equations: A WL equation or list, e.g. "x^2 - 1 == 0" or "{x+y==1, x-y==3}".
        variables: Variable or list, e.g. "x" or "{x, y}".
        numeric:   Use NSolve for numerical solutions.
    """
    for part in (equations, variables):
        _expr_filter.check(part)
    kernel = _setup()
    fn = "NSolve" if numeric else "Solve"
    return kernel.evaluate_to_string(f"{fn}[{equations}, {variables}]")


@mcp.tool()
def simplify(expression: str, full: bool = False, assumptions: str = "") -> str:
    """Simplify a mathematical expression.

    Args:
        expression:  WL expression to simplify.
        full:        Use FullSimplify (slower but more thorough).
        assumptions: Optional WL assumption, e.g. "x > 0".
    """
    _expr_filter.check(expression)
    if assumptions:
        _expr_filter.check(assumptions)
    kernel = _setup()
    fn = "FullSimplify" if full else "Simplify"
    if assumptions:
        return kernel.evaluate_to_string(f"{fn}[{expression}, {assumptions}]")
    return kernel.evaluate_to_string(f"{fn}[{expression}]")


@mcp.tool()
def integrate(
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
    _expr_filter.check(expression)
    _expr_filter.check(variable)
    if lower and upper:
        _expr_filter.check(lower)
        _expr_filter.check(upper)
    kernel = _setup()
    fn = "NIntegrate" if numeric else "Integrate"
    if lower and upper:
        return kernel.evaluate_to_string(f"{fn}[{expression}, {{{variable}, {lower}, {upper}}}]")
    return kernel.evaluate_to_string(f"{fn}[{expression}, {variable}]")


@mcp.tool()
def differentiate(expression: str, variable: str, order: int = 1) -> str:
    """Differentiate an expression with respect to a variable.

    Args:
        expression: WL expression to differentiate.
        variable:   Differentiation variable, e.g. "x".
        order:      Order of the derivative (default 1).
    """
    _expr_filter.check(expression)
    _expr_filter.check(variable)
    kernel = _setup()
    if order == 1:
        return kernel.evaluate_to_string(f"D[{expression}, {variable}]")
    return kernel.evaluate_to_string(f"D[{expression}, {{{variable}, {order}}}]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    # "setup" subcommand: generate group JSON files from local kernel
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
        kernel_path = os.environ.get("WOLFRAM_KERNEL") or None
        from mma_mcp.setup_groups import run_setup
        run_setup(kernel_path=kernel_path)
        return

    parser = argparse.ArgumentParser(description="mma-mcp Wolfram Engine MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport mode: stdio (default, for local use) or http (for remote clients)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="HTTP listen host (default: 127.0.0.1). Use 0.0.0.0 only behind a reverse proxy.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="HTTP listen port (default: 8000)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.transport == "http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        logger.info("Starting HTTP transport on %s:%d", args.host, args.port)
        mcp.run(transport="streamable-http")
    else:
        async def run_stdio() -> None:
            async with stdio_transport() as (read_stream, write_stream):
                await mcp._mcp_server.run(  # type: ignore[attr-defined]
                    read_stream,
                    write_stream,
                    mcp._mcp_server.create_initialization_options(),  # type: ignore[attr-defined]
                )

        anyio.run(run_stdio)


if __name__ == "__main__":
    main()
