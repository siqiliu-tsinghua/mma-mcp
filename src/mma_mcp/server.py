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
import threading

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
# App — encapsulates all server state (replaces global singletons)
# ---------------------------------------------------------------------------

class App:
    """Encapsulates server state: config, kernel, context, MCP server.

    Using a class instead of module-level globals makes it easy to create
    isolated instances for testing or running multiple servers.
    """

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or load_config()
        self._kernel: KernelSession | None = None
        self._ctx: ToolContext | None = None
        self._mcp: FastMCP | None = None
        self._init_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lazy-init components (double-checked locking)
    # ------------------------------------------------------------------

    @property
    def kernel(self) -> KernelSession:
        if self._kernel is None:
            with self._init_lock:
                if self._kernel is None:
                    kernel_path = find_kernel(self.config.kernel.mathkernel or None)
                    self._kernel = KernelSession(
                        kernel=kernel_path,
                        health_check_interval=self.config.kernel.health_check_interval,
                        idle_timeout=self.config.kernel.idle_timeout,
                    )
        return self._kernel

    @property
    def ctx(self) -> ToolContext:
        if self._ctx is None:
            with self._init_lock:
                if self._ctx is None:
                    self._ctx = self._build_context()
        return self._ctx

    @property
    def mcp(self) -> FastMCP:
        if self._mcp is None:
            with self._init_lock:
                if self._mcp is None:
                    self._mcp = self._create_server()
        return self._mcp

    # ------------------------------------------------------------------
    # Internal builders
    # ------------------------------------------------------------------

    def _build_role_runtimes(
        self, registry: CapabilityRegistry,
    ) -> dict[str, RoleRuntime]:
        """Build per-role permission sets and security filters."""
        from mma_mcp.tools import evaluate  # noqa: F401
        all_tool_names = frozenset(get_registered())
        config = self.config

        runtimes: dict[str, RoleRuntime] = {}
        for role_name, role_conf in config.auth.roles.items():
            # Resolve allowed tools
            if role_conf.tools == "*":
                allowed = all_tool_names
            elif isinstance(role_conf.tools, list) and role_conf.tools:
                allowed = frozenset(role_conf.tools)
            else:
                allowed = frozenset(config.tools.enabled)

            # Resolve security filter
            if role_conf.security == "none":
                expr_filter = None
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
                expr_filter = registry.build_filter(config.security)

            runtimes[role_name] = RoleRuntime(
                allowed_tools=allowed,
                expr_filter=expr_filter,
                timeout=role_conf.timeout,
                hard_timeout=role_conf.hard_timeout,
                max_result_size=role_conf.max_result_size,
            )
            logger.info(
                "Role %s: %d tools, security=%s",
                role_name, len(allowed), role_conf.security or "inherit",
            )
        return runtimes

    def _build_context(self) -> ToolContext:
        """Build the ToolContext. Kernel is NOT started here — lazy start on first use."""
        registry = CapabilityRegistry()
        expr_filter = registry.build_filter(self.config.security)
        logger.info("Security filter ready (mode: %s)", self.config.security.mode)

        role_runtimes: dict[str, RoleRuntime] = {}
        if self.config.auth.enabled:
            role_runtimes = self._build_role_runtimes(registry)
            logger.info("Built %d role runtimes", len(role_runtimes))

        return ToolContext(
            config=self.config,
            kernel=self.kernel,
            expr_filter=expr_filter,
            role_runtimes=role_runtimes,
        )

    def _create_server(self) -> FastMCP:
        """Create and configure the FastMCP server with tools from config."""
        ctx = self.ctx
        mcp = FastMCP("mma-mcp")

        if self.config.auth.enabled and ctx.role_runtimes:
            all_role_tools: set[str] = set()
            for rt in ctx.role_runtimes.values():
                all_role_tools |= rt.allowed_tools
            enabled = sorted(all_role_tools)
        else:
            enabled = self.config.tools.enabled

        registered = register_tools(mcp, ctx, enabled)
        logger.info("Server ready with %d tools: %s", len(registered), registered)
        return mcp

    # ------------------------------------------------------------------
    # HTTP auth setup
    # ------------------------------------------------------------------

    def setup_http_auth(self, app) -> None:  # noqa: ANN001
        """Mount OAuth routes and auth middleware on the Starlette app."""
        from mma_mcp.auth import BearerAuthMiddleware
        from mma_mcp.oauth import OAuthServer

        if self.config.auth.enabled:
            oauth_server = OAuthServer(auth_config=self.config.auth)
            for route in oauth_server.routes():
                app.routes.insert(0, route)
            app.add_middleware(
                BearerAuthMiddleware,
                oauth_server=oauth_server,
                auth_config=self.config.auth,
            )
            logger.info(
                "Client auth enabled (%d clients, %d roles)",
                len(self.config.auth.clients), len(self.config.auth.roles),
            )
            return

        token_env = self.config.server.auth_token_env
        if not token_env:
            return

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

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self, transport: str = "", host: str = "", port: int = 0) -> None:
        """Start the server with the given (or config-default) parameters."""
        transport = transport or self.config.server.transport
        host = host or self.config.server.host
        port = port or self.config.server.port

        mcp = self.mcp

        if transport == "http":
            import uvicorn

            app = mcp.streamable_http_app()
            self.setup_http_auth(app)

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
# Entry point
# ---------------------------------------------------------------------------

def _build_parser() -> "argparse.ArgumentParser":
    """Build the top-level CLI parser with subcommands."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="mma-mcp",
        description="Wolfram Engine MCP server",
    )
    sub = parser.add_subparsers(dest="command")

    # --- serve (default when no subcommand) ---
    serve = sub.add_parser("serve", help="Start the MCP server (default)")
    serve.add_argument(
        "--transport", choices=["stdio", "http"], default=None,
        help="Transport mode (overrides config)",
    )
    serve.add_argument("--host", default=None, help="HTTP listen host")
    serve.add_argument("--port", type=int, default=None, help="HTTP listen port")

    # --- init ---
    sub.add_parser("init", help="Generate a default mma_mcp.toml")

    # --- setup ---
    p_setup = sub.add_parser("setup", help="Generate security group JSON files from local kernel")
    p_setup.add_argument("--force", action="store_true",
                         help="Force regeneration of security groups even if they exist")

    # --- caddyfile ---
    sub.add_parser("caddyfile", help="Generate a Caddyfile for HTTPS")

    # --- hash-password ---
    sub.add_parser("hash-password", help="Hash a password for use in config")

    # --- add-client ---
    add_client = sub.add_parser(
        "add-client", help="Generate a TOML client entry (paste into mma_mcp.toml)",
    )
    add_client.add_argument("client_id", nargs="?", help="Client identifier (e.g. claude, chatgpt)")
    add_client.add_argument("--role", required=True, help="Role name")

    return parser


def main() -> None:
    import argparse

    parser = _build_parser()

    # Default to "serve" when no subcommand is given.
    # Build a local argv copy instead of mutating the global sys.argv.
    argv = sys.argv[1:]
    known_commands = {"serve", "init", "setup", "caddyfile", "hash-password", "add-client"}
    if not argv or argv[0] not in known_commands:
        argv = ["serve", *argv]

    args = parser.parse_args(argv)

    if args.command == "init":
        _cmd_init()
    elif args.command == "setup":
        _cmd_setup(force=args.force)
    elif args.command == "caddyfile":
        _cmd_caddyfile()
    elif args.command == "hash-password":
        _cmd_hash_password()
    elif args.command == "add-client":
        _cmd_add_client(args)
    else:
        _cmd_serve(args)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_serve(args) -> None:  # noqa: ANN001
    """Start the MCP server."""
    from mma_mcp.logging_config import setup_logging
    setup_logging(level=logging.INFO)
    app = App()
    app.run(
        transport=args.transport or "",
        host=args.host or "",
        port=args.port or 0,
    )


def _cmd_init() -> None:
    path = generate_default_config()
    print(f"Generated default config: {path}")


def _cmd_setup(force: bool = False) -> None:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    config = load_config()
    kernel_path = config.kernel.mathkernel or None

    from mma_mcp.setup_groups import GROUPS_DIR
    manifest_path = GROUPS_DIR / "manifest.json"
    if not force and manifest_path.exists():
        print(f"安全分组已存在 ({manifest_path})，跳过生成。")
        print("如需重新生成，请运行: mma-mcp setup --force")
        return

    from mma_mcp.setup_groups import run_setup
    run_setup(kernel_path=kernel_path)


def _cmd_caddyfile() -> None:
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


def _cmd_hash_password() -> None:
    import getpass
    from mma_mcp.passwords import hash_password
    pwd = getpass.getpass("Password: ")
    pwd2 = getpass.getpass("Confirm:  ")
    if pwd != pwd2:
        print("Error: passwords do not match", file=sys.stderr)
        sys.exit(1)
    print(hash_password(pwd))


def _cmd_add_client(args) -> None:  # noqa: ANN001
    """Generate a TOML snippet for adding an AI client."""
    import getpass
    from mma_mcp.passwords import hash_password

    client_id = args.client_id
    if not client_id:
        client_id = input("Client ID (e.g. claude, chatgpt): ")
    if not client_id:
        print("Error: client_id is required", file=sys.stderr)
        sys.exit(1)

    pwd = getpass.getpass("Password: ")
    pwd2 = getpass.getpass("Confirm:  ")
    if pwd != pwd2:
        print("Error: passwords do not match", file=sys.stderr)
        sys.exit(1)

    h = hash_password(pwd)
    print(f"\n# Add this to your mma_mcp.toml:\n")
    print(f"[auth.clients.{client_id}]")
    print(f'role = "{args.role}"')
    print(f'password_hash = "{h}"')


if __name__ == "__main__":
    main()
