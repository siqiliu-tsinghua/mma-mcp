"""MCP server entry point for mma-mcp (FastMCP-based).

Start with:
    uv run mma-mcp
or for development/inspection:
    uv run mcp dev src/mma_mcp/server.py

Configuration is read from mma_mcp.toml (preferred) or pyproject.toml [tool.mma-mcp].
Command-line flags override config file values.
"""

from __future__ import annotations

import logging
import os
import sys

import anyio
from mcp.server.fastmcp import FastMCP

from mma_mcp.config import (
    AppConfig, SecurityConfig,
    load_config, generate_default_config,
)
from mma_mcp.kernel import KernelSession, find_kernel
from mma_mcp.security.registry import CapabilityRegistry
from mma_mcp.stdio_transport import stdio_transport
from mma_mcp.tools import RoleRuntime, ToolContext, register_tools, get_registered

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singletons (initialized lazily or at startup)
# ---------------------------------------------------------------------------

_config: AppConfig | None = None
_kernel: KernelSession | None = None
_ctx: ToolContext | None = None


def _get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _get_kernel(config: AppConfig) -> KernelSession:
    """Create (but don't start) a KernelSession. Lazy start happens on first use."""
    global _kernel
    if _kernel is not None:
        return _kernel

    kernel_path = find_kernel(config.kernel.mathkernel or None)
    _kernel = KernelSession(kernel=kernel_path)
    return _kernel


# ---------------------------------------------------------------------------
# Role runtime building
# ---------------------------------------------------------------------------

def _build_role_runtimes(
    config: AppConfig,
    registry: CapabilityRegistry,
) -> dict[str, RoleRuntime]:
    """Build per-role permission sets and security filters."""
    # Ensure tool modules are imported so _REGISTRY is populated
    from mma_mcp.tools import evaluate, math  # noqa: F401
    all_tool_names = frozenset(get_registered())

    runtimes: dict[str, RoleRuntime] = {}
    for role_name, role_conf in config.auth.roles.items():
        # --- Resolve allowed tools ---
        if role_conf.tools == "*":
            allowed = all_tool_names
        elif isinstance(role_conf.tools, list) and role_conf.tools:
            allowed = frozenset(role_conf.tools)
        else:
            # Inherit from global [tools].enabled
            allowed = frozenset(config.tools.enabled)

        # --- Resolve security filter ---
        if role_conf.security == "none":
            expr_filter = None  # skip filtering
        elif role_conf.security in ("blacklist", "whitelist"):
            sec = SecurityConfig(
                mode=role_conf.security,
                deny_groups=role_conf.deny_groups,
                allow_groups=role_conf.allow_groups,
                extra_blocked=role_conf.extra_blocked,
                extra_allowed=role_conf.extra_allowed,
            )
            expr_filter = registry.build_filter(sec)
        else:
            # Inherit global [security] settings
            expr_filter = registry.build_filter(config.security)

        runtimes[role_name] = RoleRuntime(
            allowed_tools=allowed,
            expr_filter=expr_filter,
        )
        logger.info(
            "Role %s: %d tools, security=%s",
            role_name, len(allowed),
            role_conf.security or "inherit",
        )

    return runtimes


# ---------------------------------------------------------------------------
# Context building
# ---------------------------------------------------------------------------

def _build_context(config: AppConfig) -> ToolContext:
    """Build the ToolContext. Kernel is NOT started here — it starts lazily."""
    global _ctx
    if _ctx is not None:
        return _ctx

    registry = CapabilityRegistry()
    expr_filter = registry.build_filter(config.security)
    logger.info("Security filter ready (mode: %s)", config.security.mode)

    kernel = _get_kernel(config)

    # Build per-role runtimes if multi-user auth is enabled
    role_runtimes: dict[str, RoleRuntime] = {}
    if config.auth.enabled:
        role_runtimes = _build_role_runtimes(config, registry)
        logger.info("Built %d role runtimes", len(role_runtimes))

    _ctx = ToolContext(
        config=config,
        kernel=kernel,
        expr_filter=expr_filter,
        registry=registry,
        role_runtimes=role_runtimes,
    )
    return _ctx


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

def _create_server() -> FastMCP:
    """Create and configure the FastMCP server with tools from config."""
    config = _get_config()
    ctx = _build_context(config)
    mcp = FastMCP("mma-mcp")

    if config.auth.enabled and ctx.role_runtimes:
        # Register the union of all tools any role can use
        all_role_tools: set[str] = set()
        for rt in ctx.role_runtimes.values():
            all_role_tools |= rt.allowed_tools
        enabled = sorted(all_role_tools)
    else:
        enabled = config.tools.enabled

    registered = register_tools(mcp, ctx, enabled)
    logger.info("Server ready with %d tools: %s", len(registered), registered)
    return mcp


# ---------------------------------------------------------------------------
# HTTP auth setup
# ---------------------------------------------------------------------------

def _setup_http_auth(config: AppConfig, app):  # noqa: ANN001
    """Mount OAuth routes and auth middleware on the Starlette app.

    Supports two modes:
      1. Multi-user (config.auth.enabled) — OAuth + base64(user:pass)
      2. Legacy single-token (config.server.auth_token_env) — static Bearer
    """
    from mma_mcp.auth import BearerAuthMiddleware
    from mma_mcp.oauth import OAuthServer

    if config.auth.enabled:
        # Multi-user OAuth
        oauth_server = OAuthServer(auth_config=config.auth)
        for route in oauth_server.routes():
            app.routes.insert(0, route)
        app.add_middleware(
            BearerAuthMiddleware,
            oauth_server=oauth_server,
            auth_config=config.auth,
        )
        logger.info("Multi-user auth enabled (%d users, %d roles)",
                     len(config.auth.users), len(config.auth.roles))
        return

    # Legacy single-token mode
    token_env = config.server.auth_token_env
    if not token_env:
        return  # No auth

    token = os.environ.get(token_env, "")
    if not token:
        logger.error(
            "server.auth_token_env=%r is set but the env var is empty — "
            "refusing to start without a token",
            token_env,
        )
        sys.exit(1)

    oauth_server = OAuthServer(password=token)
    for route in oauth_server.routes():
        app.routes.insert(0, route)
    app.add_middleware(
        BearerAuthMiddleware, token=token, oauth_server=oauth_server,
    )
    logger.info("Legacy single-token auth enabled (env: %s)", token_env)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    # --- "setup" subcommand ---
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
        from mma_mcp.setup_groups import run_setup
        config = load_config()
        run_setup(kernel_path=config.kernel.mathkernel or None)
        return

    # --- "init" subcommand ---
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        path = generate_default_config()
        print(f"Generated default config: {path}")
        return

    # --- "caddyfile" subcommand ---
    if len(sys.argv) > 1 and sys.argv[1] == "caddyfile":
        config = load_config()
        if not config.tls.domain:
            print("Error: tls.domain must be set in config to generate a Caddyfile")
            sys.exit(1)
        from mma_mcp.caddyfile import generate_caddyfile
        path = generate_caddyfile(config)
        print(f"Generated Caddyfile: {path}")
        if config.tls.dns_provider:
            from mma_mcp.config import DNS_PROVIDERS
            info = DNS_PROVIDERS.get(config.tls.dns_provider, {})
            plugin = info.get("caddy_plugin", "")
            env_vars = info.get("env_vars", [])
            print(f"\nDNS provider: {config.tls.dns_provider}")
            print(f"Build Caddy:  xcaddy build --with {plugin}")
            print(f"Required env: {', '.join(env_vars)}")
        else:
            print("\nUsing HTTP-01 challenge (port 80 must be open)")
        return

    # --- "hash-password" subcommand ---
    if len(sys.argv) > 1 and sys.argv[1] == "hash-password":
        import getpass
        from mma_mcp.passwords import hash_password
        pwd = getpass.getpass("Password: ")
        pwd2 = getpass.getpass("Confirm:  ")
        if pwd != pwd2:
            print("Error: passwords do not match", file=sys.stderr)
            sys.exit(1)
        print(hash_password(pwd))
        return

    # --- "add-user" subcommand ---
    if len(sys.argv) > 1 and sys.argv[1] == "add-user":
        _cmd_add_user()
        return

    # --- Normal server start ---
    config = _get_config()

    parser = argparse.ArgumentParser(description="mma-mcp Wolfram Engine MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=None,
        help=f"Transport mode (config default: {config.server.transport})",
    )
    parser.add_argument(
        "--host",
        default=None,
        help=f"HTTP listen host (config default: {config.server.host})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"HTTP listen port (config default: {config.server.port})",
    )
    args = parser.parse_args()

    # CLI overrides config
    transport = args.transport or config.server.transport
    host = args.host or config.server.host
    port = args.port or config.server.port

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    mcp = _create_server()

    if transport == "http":
        import uvicorn

        app = mcp.streamable_http_app()
        _setup_http_auth(config, app)

        logger.info("Starting HTTP transport on %s:%d", host, port)
        uvi_config = uvicorn.Config(app, host=host, port=port, log_level="info")
        uvicorn.Server(uvi_config).run()
    else:
        async def run_stdio() -> None:
            async with stdio_transport() as (read_stream, write_stream):
                await mcp._mcp_server.run(  # type: ignore[attr-defined]
                    read_stream,
                    write_stream,
                    mcp._mcp_server.create_initialization_options(),  # type: ignore[attr-defined]
                )

        anyio.run(run_stdio)


# ---------------------------------------------------------------------------
# add-user CLI helper
# ---------------------------------------------------------------------------

def _cmd_add_user() -> None:
    """Generate a TOML snippet for adding a user."""
    import argparse
    import getpass
    from mma_mcp.passwords import hash_password

    parser = argparse.ArgumentParser(
        prog="mma-mcp add-user",
        description="Generate a TOML user entry (paste into mma_mcp.toml)",
    )
    parser.add_argument("username", nargs="?", help="Username")
    parser.add_argument("--role", required=True, help="Role name")
    # Consume the "add-user" token from argv
    args = parser.parse_args(sys.argv[2:])

    username = args.username
    if not username:
        username = input("Username: ")
    if not username:
        print("Error: username is required", file=sys.stderr)
        sys.exit(1)

    pwd = getpass.getpass("Password: ")
    pwd2 = getpass.getpass("Confirm:  ")
    if pwd != pwd2:
        print("Error: passwords do not match", file=sys.stderr)
        sys.exit(1)

    h = hash_password(pwd)
    print(f"\n# Add this to your mma_mcp.toml:\n")
    print(f"[auth.users.{username}]")
    print(f'role = "{args.role}"')
    print(f'password_hash = "{h}"')


if __name__ == "__main__":
    main()
