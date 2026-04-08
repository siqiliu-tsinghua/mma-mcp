"""Unified configuration for mma-mcp.

Configuration is loaded from (highest priority first):
  1. mma_mcp.toml   — standalone config in current working directory
  2. pyproject.toml  — [tool.mma-mcp] section

Use ``mma-mcp init`` to generate a default mma_mcp.toml.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class KernelConfig:
    mathkernel: str = ""        # path to MathKernel/WolframKernel; empty → auto
    timeout: int = 30           # WL-side TimeConstrained timeout in seconds; 0 = no limit
    hard_timeout: int = 60      # Python-side hard timeout — force-restart kernel if stuck; 0 = no limit
    max_result_size: int = 65536  # max result string length (chars); 0 = no limit
    session_isolation: bool = True  # isolate user variables via WL context namespacing
    default_format: str = "TeXForm"
    health_check_interval: int = 60  # seconds between health pings; 0 = disabled
    idle_timeout: int = 0            # reclaim kernel after N seconds idle; 0 = never


@dataclass
class ServerConfig:
    transport: str = "stdio"    # "stdio" or "http"
    host: str = "127.0.0.1"
    port: int = 8000
    auth_token_env: str = ""    # env var name holding the Bearer token; empty = no auth


# Known DNS providers and their required environment variables
DNS_PROVIDERS: dict[str, dict[str, Any]] = {
    "alidns": {
        "description": "Alibaba Cloud DNS",
        "caddy_plugin": "github.com/caddy-dns/alidns",
        "env_vars": ["ALIDNS_ACCESS_KEY_ID", "ALIDNS_ACCESS_KEY_SECRET"],
    },
    "cloudflare": {
        "description": "Cloudflare DNS",
        "caddy_plugin": "github.com/caddy-dns/cloudflare",
        "env_vars": ["CLOUDFLARE_API_TOKEN"],
    },
    "dnspod": {
        "description": "DNSPod (Tencent Cloud)",
        "caddy_plugin": "github.com/caddy-dns/dnspod",
        "env_vars": ["DNSPOD_API_TOKEN"],
    },
    "godaddy": {
        "description": "GoDaddy DNS",
        "caddy_plugin": "github.com/caddy-dns/godaddy",
        "env_vars": ["GODADDY_API_KEY", "GODADDY_API_SECRET"],
    },
    "namecheap": {
        "description": "Namecheap DNS",
        "caddy_plugin": "github.com/caddy-dns/namecheap",
        "env_vars": ["NAMECHEAP_API_KEY", "NAMECHEAP_API_USER"],
    },
}


@dataclass
class TlsConfig:
    enabled: bool = False
    domain: str = ""            # e.g. "mma-mcp.example.com"
    dns_provider: str = ""      # key into DNS_PROVIDERS; empty = HTTP-01 challenge
    # No API keys here — they come from environment variables.
    # See DNS_PROVIDERS for which env vars each provider needs.


@dataclass
class SecurityConfig:
    mode: str = "blacklist"     # "blacklist" or "whitelist"
    deny_groups: list[str] = field(default_factory=lambda: [
        "system_exec", "dynamic_eval", "file_write",
        "file_read", "networking", "external_services",
    ])
    allow_groups: list[str] = field(default_factory=list)
    extra_blocked: list[str] = field(default_factory=list)
    extra_allowed: list[str] = field(default_factory=list)


@dataclass
class ToolsConfig:
    enabled: list[str] = field(default_factory=lambda: [
        "evaluate",
        "evaluate_image",
    ])


@dataclass
class RoleConfig:
    """Per-role permission overrides.

    - tools: ``"*"`` = all tools, list = specific tools, ``""`` = inherit [tools].enabled
    - security: ``"none"`` = skip filtering, ``"blacklist"``/``"whitelist"`` = per-role policy,
                ``""`` = inherit global [security]
    """
    tools: list[str] | str = ""             # "*", list, or "" (inherit)
    security: str = ""                      # "none", "blacklist", "whitelist", "" (inherit)
    deny_groups: list[str] = field(default_factory=list)
    allow_groups: list[str] = field(default_factory=list)
    extra_blocked: list[str] = field(default_factory=list)
    extra_allowed: list[str] = field(default_factory=list)
    # Per-role resource limits (0 = inherit global [kernel] value)
    timeout: int = 0
    hard_timeout: int = 0
    max_result_size: int = 0


@dataclass
class ClientConfig:
    role: str = ""
    password_hash: str = ""                 # format: scrypt:<salt_hex>:<hash_hex>


@dataclass
class AuthConfig:
    enabled: bool = False
    roles: dict[str, RoleConfig] = field(default_factory=dict)
    clients: dict[str, ClientConfig] = field(default_factory=dict)


@dataclass
class AppConfig:
    kernel: KernelConfig = field(default_factory=KernelConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    tls: TlsConfig = field(default_factory=TlsConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)


# ---------------------------------------------------------------------------
# TOML loading
# ---------------------------------------------------------------------------

def _read_toml(path: Path) -> dict[str, Any]:
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib  # type: ignore[no-redef]
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _find_config_file() -> tuple[Path, dict[str, Any]] | None:
    """Locate and read the first available config source."""
    standalone = Path("mma_mcp.toml")
    if standalone.exists():
        try:
            return standalone, _read_toml(standalone)
        except Exception:
            logger.warning("Failed to parse %s", standalone, exc_info=True)

    pyproject = Path("pyproject.toml")
    if pyproject.exists():
        try:
            data = _read_toml(pyproject)
            section = data.get("tool", {}).get("mma-mcp", {})
            if section:
                return pyproject, section
        except Exception:
            logger.warning("Failed to parse %s", pyproject, exc_info=True)

    return None


def _build_kernel_config(raw: dict[str, Any]) -> KernelConfig:
    sec = raw.get("kernel", {})
    defaults = KernelConfig()
    return KernelConfig(
        mathkernel=sec.get("mathkernel", ""),
        timeout=sec.get("timeout", defaults.timeout),
        hard_timeout=sec.get("hard_timeout", defaults.hard_timeout),
        max_result_size=sec.get("max_result_size", defaults.max_result_size),
        session_isolation=sec.get("session_isolation", defaults.session_isolation),
        default_format=sec.get("default_format", defaults.default_format),
        health_check_interval=sec.get("health_check_interval", defaults.health_check_interval),
        idle_timeout=sec.get("idle_timeout", defaults.idle_timeout),
    )


def _build_server_config(raw: dict[str, Any]) -> ServerConfig:
    sec = raw.get("server", {})
    return ServerConfig(
        transport=sec.get("transport", "stdio"),
        host=sec.get("host", "127.0.0.1"),
        port=sec.get("port", 8000),
        auth_token_env=sec.get("auth_token_env", ""),
    )


def _build_tls_config(raw: dict[str, Any]) -> TlsConfig:
    sec = raw.get("tls", {})
    return TlsConfig(
        enabled=sec.get("enabled", False),
        domain=sec.get("domain", ""),
        dns_provider=sec.get("dns_provider", ""),
    )


def _build_security_config(raw: dict[str, Any]) -> SecurityConfig:
    sec = raw.get("security", {})
    defaults = SecurityConfig()
    return SecurityConfig(
        mode=sec.get("mode", defaults.mode),
        deny_groups=sec.get("deny_groups", defaults.deny_groups),
        allow_groups=sec.get("allow_groups", defaults.allow_groups),
        extra_blocked=sec.get("extra_blocked", defaults.extra_blocked),
        extra_allowed=sec.get("extra_allowed", defaults.extra_allowed),
    )


def _build_tools_config(raw: dict[str, Any]) -> ToolsConfig:
    sec = raw.get("tools", {})
    defaults = ToolsConfig()
    return ToolsConfig(
        enabled=sec.get("enabled", defaults.enabled),
    )


def _build_auth_config(raw: dict[str, Any]) -> AuthConfig:
    sec = raw.get("auth", {})
    if not sec or not sec.get("enabled", False):
        return AuthConfig()

    # Parse roles
    roles: dict[str, RoleConfig] = {}
    raw_roles = sec.get("roles", {})
    for name, rdata in raw_roles.items():
        if not isinstance(rdata, dict):
            continue
        tools_val = rdata.get("tools", "")
        roles[name] = RoleConfig(
            tools=tools_val,
            security=rdata.get("security", ""),
            deny_groups=rdata.get("deny_groups", []),
            allow_groups=rdata.get("allow_groups", []),
            extra_blocked=rdata.get("extra_blocked", []),
            extra_allowed=rdata.get("extra_allowed", []),
            timeout=rdata.get("timeout", 0),
            hard_timeout=rdata.get("hard_timeout", 0),
            max_result_size=rdata.get("max_result_size", 0),
        )

    # Parse clients
    clients: dict[str, ClientConfig] = {}
    raw_clients = sec.get("clients", {})
    for name, cdata in raw_clients.items():
        if not isinstance(cdata, dict):
            continue
        clients[name] = ClientConfig(
            role=cdata.get("role", ""),
            password_hash=cdata.get("password_hash", ""),
        )

    return AuthConfig(enabled=True, roles=roles, clients=clients)


class ConfigError(ValueError):
    """Raised when configuration is invalid."""


def _validate(config: AppConfig) -> None:
    """Validate config values. Raises ConfigError on problems."""
    errors: list[str] = []

    # kernel
    if config.kernel.timeout < 0:
        errors.append(f"kernel.timeout must be >= 0, got {config.kernel.timeout}")
    if config.kernel.hard_timeout < 0:
        errors.append(f"kernel.hard_timeout must be >= 0, got {config.kernel.hard_timeout}")
    if config.kernel.max_result_size < 0:
        errors.append(f"kernel.max_result_size must be >= 0, got {config.kernel.max_result_size}")
    valid_formats = {"TeXForm", "OutputForm", "InputForm", "StandardForm", "TraditionalForm"}
    if config.kernel.default_format not in valid_formats:
        errors.append(
            f"kernel.default_format must be one of {sorted(valid_formats)}, "
            f"got {config.kernel.default_format!r}"
        )
    if config.kernel.health_check_interval < 0:
        errors.append(f"kernel.health_check_interval must be >= 0, got {config.kernel.health_check_interval}")
    if config.kernel.idle_timeout < 0:
        errors.append(f"kernel.idle_timeout must be >= 0, got {config.kernel.idle_timeout}")
    if config.kernel.mathkernel and not Path(config.kernel.mathkernel).exists():
        errors.append(f"kernel.mathkernel path does not exist: {config.kernel.mathkernel}")
    # server
    if config.server.transport not in ("stdio", "http"):
        errors.append(
            f"server.transport must be 'stdio' or 'http', got {config.server.transport!r}"
        )
    if not (1 <= config.server.port <= 65535):
        errors.append(f"server.port must be 1-65535, got {config.server.port}")

    # tls
    if config.tls.enabled and not config.tls.domain:
        errors.append("tls.domain is required when tls.enabled = true")
    if config.tls.dns_provider and config.tls.dns_provider not in DNS_PROVIDERS:
        supported = ", ".join(sorted(DNS_PROVIDERS))
        errors.append(
            f"tls.dns_provider {config.tls.dns_provider!r} not supported. "
            f"Choose from: {supported}"
        )

    # security
    if config.security.mode not in ("blacklist", "whitelist"):
        errors.append(
            f"security.mode must be 'blacklist' or 'whitelist', "
            f"got {config.security.mode!r}"
        )
    # Warn about unknown group names (non-fatal — logged as warning)
    known_groups = set()
    groups_dir = Path(__file__).parent / "security" / "groups"
    if groups_dir.is_dir():
        known_groups = {p.stem for p in groups_dir.glob("*.json") if p.stem != "manifest"}
    for group_list_name in ("deny_groups", "allow_groups"):
        for g in getattr(config.security, group_list_name):
            if known_groups and g not in known_groups:
                logger.warning(
                    "security.%s contains unknown group %r (available: %s)",
                    group_list_name, g, sorted(known_groups),
                )

    # auth
    if config.auth.enabled:
        if not config.auth.clients:
            errors.append("auth.enabled is true but no clients are defined")
        if not config.auth.roles:
            errors.append("auth.enabled is true but no roles are defined")
        role_names = set(config.auth.roles)
        for cname, cconf in config.auth.clients.items():
            if not cconf.role:
                errors.append(f"auth.clients.{cname}: role is required")
            elif cconf.role not in role_names:
                errors.append(
                    f"auth.clients.{cname}: role {cconf.role!r} not defined "
                    f"(available: {sorted(role_names)})"
                )
            if not cconf.password_hash:
                errors.append(f"auth.clients.{cname}: password_hash is required")
            elif not cconf.password_hash.startswith("scrypt:") or cconf.password_hash.count(":") != 2:
                errors.append(
                    f"auth.clients.{cname}: password_hash must be "
                    f"'scrypt:<salt_hex>:<hash_hex>'"
                )
        valid_sec_modes = {"", "none", "blacklist", "whitelist"}
        for rname, rconf in config.auth.roles.items():
            if rconf.security not in valid_sec_modes:
                errors.append(
                    f"auth.roles.{rname}: security must be one of "
                    f"{sorted(valid_sec_modes - {''})!r} or omitted, "
                    f"got {rconf.security!r}"
                )
            if rconf.timeout < 0:
                errors.append(f"auth.roles.{rname}: timeout must be >= 0")
            if rconf.hard_timeout < 0:
                errors.append(f"auth.roles.{rname}: hard_timeout must be >= 0")
            if rconf.max_result_size < 0:
                errors.append(f"auth.roles.{rname}: max_result_size must be >= 0")

    if errors:
        msg = "Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ConfigError(msg)


def load_config() -> AppConfig:
    """Load configuration from the first available source, or return defaults."""
    result = _find_config_file()
    if result is None:
        logger.info("No config file found, using defaults")
        return AppConfig()

    path, raw = result
    logger.info("Loaded config from %s", path)
    config = AppConfig(
        kernel=_build_kernel_config(raw),
        server=_build_server_config(raw),
        tls=_build_tls_config(raw),
        security=_build_security_config(raw),
        tools=_build_tools_config(raw),
        auth=_build_auth_config(raw),
    )
    _validate(config)
    return config


# ---------------------------------------------------------------------------
# Default config generation (mma-mcp init)
# ---------------------------------------------------------------------------

_DEFAULT_TOML = """\
# mma-mcp configuration file
# Generated by: mma-mcp init
# Documentation: see CLAUDE.md

# ─── Wolfram Kernel ──────────────────────────────────────────────────────────

[kernel]
# Path to MathKernel / WolframKernel binary.
# Leave empty to auto-detect (searches WOLFRAM_KERNEL env → which → common paths).
mathkernel = ""

# Per-evaluation timeout in seconds (Wolfram Language TimeConstrained).
# The kernel cooperatively aborts the computation and returns $Aborted.
# 0 = no timeout.
timeout = 30

# Hard timeout in seconds — Python-side safety net.
# If the kernel does not respond within this time (e.g. stuck in C code),
# it is forcibly restarted. Should be larger than timeout. 0 = no limit.
hard_timeout = 60

# Maximum result string length in characters. Results exceeding this limit
# are truncated with a warning. Prevents huge outputs from overwhelming
# MCP responses. 0 = no limit.
max_result_size = 65536

# Session isolation: each authenticated AI client gets a separate WL context
# namespace, so variables defined by one client are invisible to others.
# Has no effect in single-client (stdio / no auth) mode.
session_isolation = true

# Default output format: TeXForm, OutputForm, InputForm, etc.
default_format = "TeXForm"

# Health check interval in seconds. A background thread pings the kernel
# periodically; if it doesn't respond, the kernel is auto-restarted.
# 0 = disabled.
health_check_interval = 60

# Idle timeout in seconds. If no evaluation runs for this long, the kernel
# is stopped to free resources. It restarts automatically on the next request.
# 0 = never reclaim.
idle_timeout = 0

# ─── Server Transport ────────────────────────────────────────────────────────

[server]
# Transport mode: "stdio" (local MCP clients) or "http" (HTTP MCP clients)
transport = "stdio"

# HTTP listen address (only used when transport = "http")
# Use 127.0.0.1 behind a reverse proxy; use 0.0.0.0 for direct exposure (not recommended)
host = "127.0.0.1"
port = 8000

# Bearer token authentication (HTTP transport only).
# Set to the name of an environment variable that holds the secret token.
# Requests must include "Authorization: Bearer <token>" header.
# Leave empty to disable auth (e.g. when Caddy handles auth upstream).
# auth_token_env = "MMA_MCP_AUTH_TOKEN"

# ─── TLS / Reverse Proxy ─────────────────────────────────────────────────────
# Only relevant when server.transport = "http".
# mma-mcp can generate a Caddyfile for automatic HTTPS via Let's Encrypt.

[tls]
enabled = false

# Your domain for HTTPS, e.g. "mma-mcp.example.com"
domain = ""

# DNS provider for DNS-01 ACME challenge (no need to open port 80).
# Leave empty to use HTTP-01 challenge (requires port 80 open).
#
# Supported providers:
#   "alidns"     — Alibaba Cloud DNS    (env: ALIDNS_ACCESS_KEY_ID, ALIDNS_ACCESS_KEY_SECRET)
#   "cloudflare" — Cloudflare DNS       (env: CLOUDFLARE_API_TOKEN)
#   "dnspod"     — DNSPod / Tencent     (env: DNSPOD_API_TOKEN)
#   "godaddy"    — GoDaddy DNS          (env: GODADDY_API_KEY, GODADDY_API_SECRET)
#   "namecheap"  — Namecheap DNS        (env: NAMECHEAP_API_KEY, NAMECHEAP_API_USER)
#
# API credentials are NEVER stored in this file — set them as environment
# variables or in a systemd EnvironmentFile. See docs for details.
dns_provider = ""

# ─── Security ─────────────────────────────────────────────────────────────────

[security]
# Security mode: "blacklist" (deny listed groups) or "whitelist" (allow listed groups only)
mode = "blacklist"

# Blacklist mode: groups whose symbols are forbidden.
deny_groups = [
    "system_exec",
    "dynamic_eval",
    "file_write",
    "file_read",
    "networking",
    "external_services",
]

# Whitelist mode: only these groups' symbols are allowed.
# Used when mode = "whitelist". Example:
# allow_groups = [
#     "math_core",
#     "algebra",
#     "calculus",
#     "linear_algebra",
#     "statistics",
#     "number_theory",
#     "combinatorics",
#     "data_structures",
#     "programming",
#     "visualization",
#     "graph_theory",
#     "geometry",
#     "optimization",
#     "signal_processing",
#     "image",
#     "machine_learning",
#     "chemistry_biology",
#     "quantitative",
#     "compile",
#     "crypto",
#     "fractal",
#     "interpolation",
# ]

# Fine-grained overrides (optional)
# extra_blocked = ["Symbol1", "Symbol2"]
# extra_allowed = ["Symbol3"]

# ─── MCP Tools ────────────────────────────────────────────────────────────────

[tools]
# MCP tools to expose to clients.
# evaluate: returns text results; evaluate_image: returns PNG images.
enabled = [
    "evaluate",
    "evaluate_image",
]

# ─── Authentication & Roles ─────────────────────────────────────────────────
# Client identity and role-based access control for AI client isolation.
# Each AI client (e.g. Claude, ChatGPT) connects with its own credentials
# and is bound to a role that controls tool access and resource limits.
# Generate password hashes with: mma-mcp hash-password

# [auth]
# enabled = true
#
# [auth.roles.admin]
# tools = "*"              # all tools
# security = "none"        # no symbol filtering
#
# [auth.roles.standard]
# # Inherits [tools].enabled and [security] settings (nothing to configure)
#
# [auth.roles.analyst]
# tools = ["evaluate"]  # text only, no image output
# security = "whitelist"
# allow_groups = ["math_core", "algebra", "calculus", "statistics"]
# # Per-role resource limits (0 or omitted = inherit global [kernel] values)
# timeout = 15
# hard_timeout = 30
# max_result_size = 16384
#
# [auth.clients.claude]
# role = "admin"
# password_hash = "scrypt:<salt_hex>:<hash_hex>"
#
# [auth.clients.chatgpt]
# role = "analyst"
# password_hash = "scrypt:<salt_hex>:<hash_hex>"
"""


def generate_default_config(target: Path | None = None) -> Path:
    """Write a default mma_mcp.toml and return its path."""
    target = target or Path("mma_mcp.toml")
    target.write_text(_DEFAULT_TOML, encoding="utf-8")
    return target
