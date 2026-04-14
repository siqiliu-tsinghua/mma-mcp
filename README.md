# mma-mcp

[Chinese / 中文版](README-cn.md)

A [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server that wraps a local **Wolfram Engine**, enabling AI assistants (Claude, ChatGPT, etc.) to perform symbolic math, numerical analysis, and data visualization via Wolfram Language.

> **Disclaimer:** This is an **unofficial**, independent, personal project.
> It is **not** affiliated with, sponsored by, endorsed by, or certified by
> Wolfram Research, Inc.  "Wolfram", "Wolfram Language", "Wolfram Engine",
> "Mathematica", and related marks are trademarks of Wolfram Research.
>
> This software does **not** include any Wolfram Engine / Mathematica binaries,
> activation keys, license files, or other proprietary materials.  Users must
> independently obtain and properly license their own copy of the Wolfram
> Engine or Mathematica in accordance with
> [Wolfram's licensing terms](https://www.wolfram.com/legal/).
>
> The sole purpose of this project is to allow a **licensed individual** to
> invoke their own, locally-installed Wolfram kernel through AI assistants
> on their own machine, within the scope permitted by their license.
> **Redistribution of Wolfram Engine access to third parties is not an
> intended use case and may violate Wolfram's licensing terms.**

## Features

- **MCP Tools**: `evaluate` (text) and `evaluate_image` (PNG) — all Wolfram Language capabilities through two universal tools
- **Transports**: stdio (local) and Streamable HTTP
- **Security**: Pre-kernel expression filtering with blacklist/whitelist modes and 29 capability groups
- **Client RBAC**: Per-client credentials, per-role tool and security policy control — for isolating different AI clients on the same machine
- **OAuth 2.1**: Authorization server for web-based MCP clients (Claude.ai, ChatGPT)
- **Config-driven**: Single TOML file controls all behavior

## Prerequisites

- Python 3.11+
- [Wolfram Engine](https://www.wolfram.com/engine/) or Mathematica (properly licensed)
- [uv](https://docs.astral.sh/uv/) package manager

## Quick Start

```bash
# Clone and install
git clone https://github.com/<owner>/mma-mcp.git
cd mma-mcp
uv sync

# Graphics export dependencies (headless servers only — desktops already have these)
sudo apt-get install -y libfontconfig1 libgl1 libasound2t64 libxkbcommon0 libegl1

# Generate default config
uv run mma-mcp init

# Generate security group files (requires Wolfram kernel, ~1 min)
uv run mma-mcp setup

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

### Claude Desktop (stdio)

Add to your `claude_desktop_config.json` (Settings -> Developer -> Edit Config):

```json
{
  "mcpServers": {
    "mma-mcp": {
      "command": "/path/to/mma-mcp/.venv/bin/mma-mcp"
    }
  }
}
```

> On macOS/Linux, find the config at `~/Library/Application Support/Claude/claude_desktop_config.json` or `~/.config/Claude/claude_desktop_config.json`.

### HTTP Transport

```bash
uv run mma-mcp serve --transport http --host 127.0.0.1 --port 8000
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
| `[auth]` | Client identity and role-based access control |

## Security

Expressions are filtered **before** reaching the Wolfram kernel. Symbols are extracted via regex and checked against the active policy.

**Blacklist mode** (default): blocks dangerous groups (system_exec, file I/O, networking, dynamic eval).

**Whitelist mode**: only allows symbols from explicitly enabled groups.

29 capability groups (22 safe + 7 dangerous) cover ~6000 Wolfram Language symbols. Regenerate from your local kernel:

```bash
uv run mma-mcp setup          # required after cloning (generates from your local kernel)
uv run mma-mcp setup --force   # force regeneration (e.g., after Wolfram Engine upgrade)
```

## Client Identity & Roles

When using HTTP transport, you can configure per-client credentials and roles to isolate different AI clients (e.g., Claude and ChatGPT) connecting to the same kernel:

```bash
# Generate password hash
uv run mma-mcp hash-password

# Generate TOML snippet for a new client
uv run mma-mcp add-client claude --role admin
```

Each client is bound to a role that controls which tools it can access, which Wolfram symbols it can use, and resource limits (timeout, result size).  Concurrent clients are isolated via a kernel worker pool — each tool call runs in an exclusive kernel process with a temporary WL context.

See the `[auth]` section in `mma_mcp.toml` for configuration details.

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
| `mma-mcp setup` | Generate security group JSONs from local kernel |
| `mma-mcp caddyfile` | Generate Caddyfile for HTTPS |
| `mma-mcp hash-password` | Hash a password for config |
| `mma-mcp add-client` | Generate TOML snippet for a new AI client |

## License

MIT — applies only to the code in this repository.  Use of Wolfram Engine /
Mathematica is governed by Wolfram Research's own license terms.
