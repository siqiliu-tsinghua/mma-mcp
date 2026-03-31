# mma-mcp — Wolfram Engine MCP Server

## Working Language

Preferred: **Chinese (Simplified)**. Fallback: English. Never use other languages.

## Project Overview

A Model Context Protocol (MCP) server that wraps a local Wolfram Engine, enabling AI assistants (Claude, etc.) to invoke Wolfram Language computation — symbolic math, numerical analysis, data visualization, and more.

## Tech Stack

- **Language:** Python 3.11+
- **MCP framework:** `mcp[cli]` (official Python SDK)
- **Wolfram bridge:** `wolframclient` (local kernel via `WolframLanguageSession`)
- **Package manager:** `uv`

## Project Structure

```
mma-mcp/
├── src/
│   └── mma_mcp/
│       ├── __init__.py
│       ├── server.py              # MCP server entry point
│       ├── kernel.py              # Wolfram kernel lifecycle management
│       ├── security/
│       │   ├── __init__.py
│       │   ├── filter.py          # ExpressionFilter: AST parsing + symbol checking
│       │   ├── registry.py        # CapabilityRegistry: load & merge group definitions
│       │   └── groups/            # Pre-generated JSON symbol lists per group
│       │       ├── manifest.json  # Group metadata (description, danger level)
│       │       ├── arithmetic.json
│       │       ├── algebra.json
│       │       ├── calculus.json
│       │       ├── linear_algebra.json
│       │       ├── statistics.json
│       │       ├── number_theory.json
│       │       ├── special_functions.json
│       │       ├── combinatorics.json
│       │       ├── list_ops.json
│       │       ├── string_ops.json
│       │       ├── programming.json
│       │       ├── plotting_2d.json
│       │       ├── plotting_3d.json
│       │       ├── graphics.json
│       │       ├── file_read.json       # dangerous
│       │       ├── file_write.json      # dangerous
│       │       ├── networking.json      # dangerous
│       │       ├── system_exec.json     # dangerous
│       │       ├── dynamic_eval.json    # dangerous
│       │       └── external_services.json  # dangerous
│       ├── tools/
│       │   ├── evaluate.py        # evaluate / evaluate_image
│       │   ├── math.py            # solve / simplify / integrate
│       │   └── query.py           # WolframAlpha-style queries
│       └── utils.py               # Result formatting, error handling
├── scripts/
│   └── generate_groups.wl         # Regenerate group JSONs from local kernel
├── tests/
├── pyproject.toml
└── CLAUDE.md
```

## Architecture Decisions

- **Kernel session:** Use a persistent `WolframLanguageSession` (stateful, long-lived) rather than per-request kernels. Supports `session_id` for multi-session isolation.
- **Image output:** Export WL graphics via `Export[..., "PNG"]`, return as base64-encoded `ImageContent` in MCP responses.
- **Result format:** Default to `OutputForm`; expose `TeXForm` and `StandardForm` as options.
- **Error handling:** Catch `WolframKernelException`; auto-restart kernel on crash.

## Security Architecture

### Core principle
Expression filtering happens **before** the kernel sees any code. The Python layer parses the WL expression into an AST, extracts all symbol references, and checks against the active policy. The kernel only receives clean, policy-compliant expressions.

### Two-layer symbol resolution
1. **`DANGEROUS_SYMBOLS`** — a single authoritative set maintained in `filter.py` (~few hundred symbols covering system_exec, file I/O, networking, dynamic eval)
2. **All built-ins** — queried from kernel once at startup via `Names["System`*"]`

Derived policies:
- **Blacklist mode:** reject if `used_symbols ∩ DANGEROUS_SYMBOLS ≠ ∅`
- **Whitelist mode:** reject if `used_symbols ⊄ (all_builtins − DANGEROUS_SYMBOLS)`

### Capability groups
Symbols are pre-grouped into named capability groups (stored as JSON files). Users configure security by enabling/disabling groups, not individual symbols.

**Safe groups** (enabled by default in whitelist mode):
`arithmetic`, `algebra`, `calculus`, `linear_algebra`, `statistics`, `number_theory`, `special_functions`, `combinatorics`, `list_ops`, `string_ops`, `programming`, `plotting_2d`, `plotting_3d`, `graphics`

**Dangerous groups** (blocked by default):
`file_read`, `file_write`, `networking`, `system_exec`, `dynamic_eval`, `external_services`

### Edge case: dynamic symbol construction
`Symbol["Run"]` and similar patterns are handled by special-casing `Symbol` calls during AST traversal — string arguments are treated as symbol names and checked against the policy.

`ToExpression` is always in `dynamic_eval` (dangerous) and blocked by default in both modes.

### Runtime flow
```
CapabilityRegistry.load()      # startup: read all group JSONs → frozensets
  → resolve(config)            # merge allow/deny groups → single frozenset
  → ExpressionFilter(policy)   # bind policy

per request:
  parse(expr_str) → AST
  → extract_symbols(AST) → set[str]
  → filter.check(symbols)      # frozenset intersection/difference, O(n)
  → session.evaluate(expr)
```

### User configuration (pyproject.toml / config file)
```toml
[security]
mode = "whitelist"
allow_groups = ["arithmetic", "algebra", "calculus", "plotting_2d"]

# or:
mode = "blacklist"
deny_groups = ["system_exec", "networking", "file_write", "dynamic_eval"]
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `evaluate` | Execute arbitrary WL expression, return string result |
| `evaluate_image` | Execute WL expression, return PNG image (for plots/graphics) |
| `solve` | Solve equations or systems of equations |
| `simplify` | Simplify mathematical expressions |
| `integrate` | Symbolic or numerical integration |

## Deployment Scenario

目标运行环境：**公网 Linux 主机 + HTTPS + 远程 MCP 客户端（Claude 网页版、ChatGPT 等）**

### 传输层

HTTP 模式使用 MCP Streamable HTTP 传输：

```bash
mma-mcp --transport http --host 127.0.0.1 --port 8000
```

`--host 127.0.0.1` 表示只监听本地，由 Caddy 做 TLS 终结后反向代理进来。

### 反向代理：Caddy + alidns 插件

域名在阿里云，使用 DNS-01 验证申请 Let's Encrypt 证书（不需要开放 80 端口）。

构建带插件的 Caddy：

```bash
xcaddy build --with github.com/caddy-dns/alidns
```

配置见项目根目录的 `Caddyfile.example`。

RAM 子账号需要 `AliyunDNSFullAccess` 权限（或最小化 DNS 记录写入权限）。

### 客户端配置

```json
{
  "mcpServers": {
    "mma-mcp": {
      "url": "https://mma-mcp.yourdomain.com/mcp"
    }
  }
}
```

### systemd 服务（参考）

```ini
[Unit]
Description=mma-mcp Wolfram Engine MCP Server
After=network.target

[Service]
User=mma
WorkingDirectory=/opt/mma-mcp
ExecStart=/opt/mma-mcp/.venv/bin/mma-mcp --transport http --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## Development Phases

- **Phase 1:** Project scaffold + `kernel.py` + `security/` + minimal `evaluate` tool
- **Phase 2:** Full tool set + image output
- **Phase 3:** Robustness (auto-restart, session persistence)
- **Phase 4:** Integration testing with Claude Desktop / Claude Code

## WSL 开发注意事项

- **严禁运行 `wolframscript -activate`**：免费版 Wolfram Engine 有单机激活数量限制。WSL 每次重启 MAC 地址会变，重复激活会触发"single machine process limit reached"，导致 license 锁死数小时。如遇 license 失效，联系机器管理员手动处理，不要自行激活。
- WSL 重启后若内核报 "No valid password found"，属于已知 WSL 环境问题，等待 license 服务器释放或重新配置 WSL 网络。

## Conventions

- All tools must handle kernel errors gracefully and return user-readable error messages (never crash the MCP server).
- Prefer `wolframclient` native Python types over raw string parsing where possible.
- Tests go in `tests/`, use `pytest`.
- No external Wolfram Cloud calls — local Engine only.
- Group JSON files are pre-generated and committed; use `scripts/generate_groups.wl` to refresh after a Wolfram Engine upgrade.
