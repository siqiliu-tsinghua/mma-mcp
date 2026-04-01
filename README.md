# mma-mcp

A [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server that wraps a local **Wolfram Engine**, enabling AI assistants (Claude, ChatGPT, etc.) to perform symbolic math, numerical analysis, and data visualization via Wolfram Language.

## Features

- **MCP Tools**: `evaluate`, `evaluate_image`, `solve`, `simplify`, `integrate`, `differentiate`
- **Transports**: stdio (local) and Streamable HTTP (remote)
- **Security**: Pre-kernel expression filtering with blacklist/whitelist modes and 20 capability groups
- **Multi-user RBAC**: Per-user credentials, per-role tool and security policy control
- **OAuth 2.1**: Authorization server for web MCP clients (Claude.ai, ChatGPT)
- **Config-driven**: Single TOML file controls all behavior

## Prerequisites

- Python 3.11+
- [Wolfram Engine](https://www.wolfram.com/engine/) (free for non-commercial use)
- [uv](https://docs.astral.sh/uv/) package manager

## Quick Start

```bash
# Clone and install
git clone https://github.com/your-org/mma-mcp.git
cd mma-mcp
uv sync

# Generate default config
uv run mma-mcp init

# Start server (stdio, for local MCP clients)
uv run mma-mcp serve
```

## Client Configuration

### Claude Code / VS Code (stdio)

Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "mma-mcp": {
      "command": "uv",
      "args": ["--directory", "/path/to/mma-mcp", "run", "mma-mcp"]
    }
  }
}
```

### Remote Clients (HTTP)

```bash
uv run mma-mcp serve --transport http --host 127.0.0.1 --port 8000
```

Then configure your MCP client with:

```json
{
  "mcpServers": {
    "mma-mcp": {
      "url": "https://mma-mcp.yourdomain.com/mcp"
    }
  }
}
```

## Configuration

All settings live in `mma_mcp.toml` (or `pyproject.toml` under `[tool.mma-mcp]`).

```bash
uv run mma-mcp init  # generates mma_mcp.toml with comments
```

Key sections:

| Section | Description |
|---------|-------------|
| `[kernel]` | Wolfram kernel path, timeout, output format |
| `[server]` | Transport mode, host, port |
| `[security]` | Blacklist/whitelist mode, capability groups |
| `[tools]` | Which MCP tools to expose |
| `[tls]` | Domain and DNS provider for HTTPS (Caddy) |
| `[auth]` | Multi-user roles and credentials |

## Security

Expressions are filtered **before** reaching the Wolfram kernel. Symbols are extracted via regex and checked against the active policy.

**Blacklist mode** (default): blocks dangerous groups (system_exec, file I/O, networking, dynamic eval).

**Whitelist mode**: only allows symbols from explicitly enabled groups.

20 pre-built capability groups (14 safe + 6 dangerous) cover ~2000 Wolfram Language symbols. Regenerate from your local kernel:

```bash
uv run mma-mcp setup
```

## Multi-User Authentication

For public-facing deployments, enable per-user auth with role-based access control:

```bash
# Generate password hash
uv run mma-mcp hash-password

# Generate TOML snippet for a new user
uv run mma-mcp add-user alice --role admin
```

See the `[auth]` section in `mma_mcp.toml` for configuration details.

## Deployment

### With Caddy (HTTPS)

```bash
# Generate Caddyfile from config
uv run mma-mcp caddyfile

# Build Caddy with DNS plugin (e.g., for Alibaba Cloud DNS)
xcaddy build --with github.com/caddy-dns/alidns
```

### With systemd

Copy `mma-mcp.service` to `/etc/systemd/system/` and adjust paths:

```bash
sudo cp mma-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mma-mcp
```

## Development

```bash
# Run tests
uv run pytest tests/ -v

# Inspect MCP tools interactively
uv run mcp dev src/mma_mcp/server.py
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `mma-mcp serve` | Start the MCP server (default) |
| `mma-mcp init` | Generate default `mma_mcp.toml` |
| `mma-mcp setup` | Regenerate security group JSONs from local kernel |
| `mma-mcp caddyfile` | Generate Caddyfile for reverse proxy + HTTPS |
| `mma-mcp hash-password` | Hash a password for config |
| `mma-mcp add-user` | Generate TOML snippet for a new user |

## License

MIT
