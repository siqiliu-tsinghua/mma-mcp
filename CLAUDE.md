# mma-mcp — Wolfram Engine MCP Server

## Working Language

Preferred: **Chinese (Simplified)**. Fallback: English. Never use other languages.

## Project Overview

A Model Context Protocol (MCP) server that wraps a local Wolfram Engine, enabling AI assistants (Claude, ChatGPT, etc.) to invoke Wolfram Language computation — symbolic math, numerical analysis, data visualization, and more.

## Tech Stack

- **Language:** Python 3.11+
- **MCP framework:** `mcp[cli]` (official Python SDK, FastMCP)
- **Wolfram bridge:** `wolframclient` (local kernel via `WolframLanguageSession`)
- **HTTP:** Starlette + uvicorn (via `mcp[cli]` transitive deps)
- **Package manager:** `uv`

## Project Structure

```
mma-mcp/
├── src/
│   └── mma_mcp/
│       ├── __init__.py
│       ├── server.py              # App class + CLI entry point (argparse subcommands)
│       ├── config.py              # TOML config loading, dataclasses, validation
│       ├── kernel.py              # KernelSession: single kernel lifecycle, auto-restart, timeout
│       ├── pool.py               # KernelPool: worker pool for cross-client isolation
│       ├── auth.py                # BearerAuthMiddleware, ClientIdentity contextvar
│       ├── oauth.py               # Minimal OAuth 2.1 server (DCR + PKCE + AuthCode)
│       ├── passwords.py           # scrypt hash/verify (stdlib only)
│       ├── logging_config.py      # Structured logging with per-request ID
│       ├── stdio_transport.py     # Custom stdio transport (fixes SDK pipe hang)
│       ├── caddyfile.py           # Caddyfile generator for HTTPS deployment
│       ├── setup_groups.py        # Generate security group JSONs from local kernel
│       ├── security/
│       │   ├── __init__.py
│       │   ├── filter.py          # ExpressionFilter: regex symbol extraction + policy check
│       │   ├── registry.py        # CapabilityRegistry: load groups, build filters
│       │   └── groups/            # Pre-generated JSON symbol lists per group
│       │       ├── manifest.json  # Group metadata (28 groups: 22 safe + 6 dangerous)
│       │       ├── math_core.json, algebra.json, ...  # 22 safe groups
│       │       ├── system_exec.json, file_read.json, ...  # 6 dangerous groups
│       │       └── (regenerate via: mma-mcp setup)
│       └── tools/
│           ├── __init__.py        # Tool registry, ToolContext, RoleRuntime, RBAC wrapper
│           └── evaluate.py        # evaluate (text) / evaluate_image (PNG)
├── tests/
│   ├── test_security.py           # Filter + registry unit tests
│   ├── test_config.py             # Config loading/validation tests
│   ├── test_auth.py               # Auth + OAuth + password tests
│   ├── test_tools.py              # Tool registry + RBAC + session isolation tests
│   ├── test_integration.py        # Real kernel integration tests
│   └── test_mcp_e2e.py            # Full MCP protocol end-to-end tests
├── scripts/
│   └── generate_groups.wl         # Pure WL alternative for group generation
├── pyproject.toml
├── CLAUDE.md
├── ARCHITECTURE.md                # Detailed architecture documentation (Chinese)
├── DEPLOY.md                      # VPS deployment guide
└── README.md
```

## Architecture Overview

### Layered security model
```
Layer 1: Authentication (auth.py / oauth.py)
  └─ Bearer token / OAuth 2.1 → client identity

Layer 2: Role-based access control (tools/__init__.py)
  └─ Per-role tool permissions → which MCP tools can be called

Layer 3: Expression filtering (security/)
  └─ Per-role symbol policy → which WL functions can be used
```

### Key design decisions

- **Pre-kernel filtering:** Expressions are filtered in Python (regex symbol extraction) before the kernel sees them. The kernel only receives policy-compliant code.
- **Worker pool isolation:** Each tool call acquires an exclusive kernel worker from a pool (`KernelPool`). Workers are stateless — a temporary WL context is used per call and cleaned up on release. This provides process-level isolation between concurrent clients (no cross-client `Contexts[]`/`Names[]`/`UpValues` attacks). Pool supports lazy creation, idle reclaim, and periodic worker restart.
- **Two-layer timeout:** WL-side `TimeConstrained` (cooperative) + Python-side `ThreadPoolExecutor` hard timeout (force-restart on stuck kernel).
- **Config-driven:** All behavior controlled via `mma_mcp.toml`. Tools, security policy, auth, resource limits — all configurable without code changes.
- **Contextvar-based RBAC:** `current_client` and `_active_filter` contextvars propagate per-request identity and security policy, concurrent-safe.
- **Stateless evaluation:** Each tool call uses a temporary WL context (`Pool$<random>\``) that is cleaned up after execution. AI clients generate self-contained expressions (using `Module`/`With`/`Block` for local state).

## Security Architecture

### Expression filtering (security/filter.py)

Multi-pass regex tokenizer:
1. Detect `Symbol["X"]` patterns and `<<` (Get) operator before stripping
2. Strip string literals (`"..."`) and WL comments (`(* ... *)`)
3. Extract all symbol identifiers, normalize context-qualified names (`System\`Run` → `Run`)
4. Check extracted symbols against active policy (blacklist/whitelist)

**Known limitation:** Dynamic string concatenation like `ToExpression["Ru" <> "n"]` cannot be statically detected. Mitigated by blocking `ToExpression` in `dynamic_eval` group.

### Capability groups (security/groups/)

28 groups derived from WolframLanguageData FunctionalityAreas + hard-coded dangerous seeds:

- **Safe (22):** math_core, algebra, calculus, linear_algebra, statistics, number_theory, combinatorics, data_structures, programming, visualization, graph_theory, geometry, optimization, signal_processing, image, machine_learning, chemistry_biology, quantitative, compile, crypto, fractal, interpolation
- **Dangerous (6):** system_exec, dynamic_eval, file_read, file_write, networking, external_services

### Security modes

- **Blacklist (default):** Block symbols in `deny_groups`, allow everything else. More flexible.
- **Whitelist:** Only allow symbols in `allow_groups`, block everything else. More secure.

Each role can independently choose mode and groups, or inherit the global setting.

## Authentication & Authorization

### Three auth modes (auto-selected)

1. **Multi-client OAuth** (`[auth] enabled = true`): Client ID + password, per-role permissions, OAuth 2.1 for web MCP clients
2. **Legacy single-token** (`server.auth_token_env`): Static Bearer token from env var
3. **No auth** (stdio): No middleware mounted

### OAuth 2.1 (oauth.py)
- RFC 8414 metadata discovery, RFC 7591 DCR, RFC 7636 PKCE (S256)
- Authorization Code grant with login page
- In-memory token store with TTL and capacity limits

### Password hashing (passwords.py)
- stdlib `hashlib.scrypt` (N=16384, r=8, p=1), timing-safe verification
- Format: `scrypt:<salt_hex>:<hash_hex>`

## MCP Tools

| Tool | Description |
|------|-------------|
| `evaluate` | Execute any WL expression, return text result (TeXForm/OutputForm/etc.) |
| `evaluate_image` | Execute any WL expression, return PNG image (for plots/graphics) |

All Wolfram Language capabilities are accessed through these two universal tools.

## CLI Commands

| Command | Description |
|---------|-------------|
| `mma-mcp serve` | Start the MCP server (default) |
| `mma-mcp init` | Generate default `mma_mcp.toml` |
| `mma-mcp setup` | Generate security group JSONs from local kernel |
| `mma-mcp caddyfile` | Generate Caddyfile for HTTPS deployment |
| `mma-mcp hash-password` | Hash a password for config |
| `mma-mcp add-client` | Generate TOML snippet for a new AI client |

## Deployment

- **stdio:** Local MCP clients (Claude Desktop, Claude Code, VS Code)
- **HTTP:** `mma-mcp serve --transport http`, behind Caddy for TLS termination

详见 `DEPLOY.md` 和 `ARCHITECTURE.md`。

## WSL 开发注意事项

- **严禁运行 `wolframscript -activate`**：免费版 Wolfram Engine 有单机激活数量限制。WSL 每次重启 MAC 地址会变，重复激活会触发"single machine process limit reached"，导致 license 锁死数小时。如遇 license 失效，联系机器管理员手动处理，不要自行激活。
- WSL 重启后若内核报 "No valid password found"，属于已知 WSL 环境问题，等待 license 服务器释放或重新配置 WSL 网络。

## Conventions

- All tools must handle kernel errors gracefully and return user-readable error messages (never crash the MCP server).
- Prefer `wolframclient` native Python types over raw string parsing where possible.
- Tests go in `tests/`, use `pytest`. Integration tests marked with `@pytest.mark.integration`.
- No external Wolfram Cloud calls — local Engine only.
- Group JSON files are pre-generated and committed; use `mma-mcp setup` to refresh after a Wolfram Engine upgrade.
