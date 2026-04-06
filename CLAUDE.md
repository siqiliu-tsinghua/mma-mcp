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
│       │   └── groups/            # WLD-derived JSON symbol lists per group
│       │       ├── manifest.json  # Group metadata (28 groups: 22 safe + 6 dangerous)
│       │       ├── math_core.json, algebra.json, calculus.json, ...  # 22 safe groups
│       │       ├── system_exec.json, file_read.json, ...             # 6 dangerous groups
│       │       └── (regenerate via: mma-mcp setup)
│       ├── tools/
│       │   ├── evaluate.py        # evaluate / evaluate_image
│       │   ├── math.py            # solve / simplify / integrate / differentiate
│       │   ├── plot.py            # plot (14 plot types → PNG)
│       │   ├── data.py            # data_query (20 curated data sources)
│       │   └── query.py           # WolframAlpha natural language query
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
- **Timeout:** Two-layer timeout — WL-side `TimeConstrained` (cooperative, configurable via `kernel.timeout`) + Python-side hard timeout via `ThreadPoolExecutor` (force-restart kernel, configurable via `kernel.hard_timeout`).
- **Result size limit:** `kernel.max_result_size` truncates oversized results before returning to MCP client.

## Security Architecture

### Core principle
Expression filtering happens **before** the kernel sees any code. The Python layer parses the WL expression into an AST, extracts all symbol references, and checks against the active policy. The kernel only receives clean, policy-compliant expressions.

### Symbol classification
Symbols are classified using **WolframLanguageData FunctionalityAreas** as the primary source. Each of the 208 distinct FunctionalityAreas maps to a security group. Hard-coded dangerous seeds provide a safety net for critical symbols regardless of WLD data.

Derived policies:
- **Blacklist mode:** reject if `used_symbols ∩ dangerous_symbols ≠ ∅`
- **Whitelist mode:** reject if `used_symbols ⊄ allowed_symbols`

### Capability groups
Symbols are pre-grouped into named capability groups (stored as JSON files). Users configure security by enabling/disabling groups, not individual symbols. Run `mma-mcp setup` to regenerate groups from the local kernel.

**Safe groups** (22, enabled by default in whitelist mode):
`math_core`, `algebra`, `calculus`, `linear_algebra`, `statistics`, `number_theory`, `combinatorics`, `data_structures`, `programming`, `visualization`, `graph_theory`, `geometry`, `optimization`, `signal_processing`, `image`, `machine_learning`, `chemistry_biology`, `quantitative`, `compile`, `crypto`, `fractal`, `interpolation`

**Dangerous groups** (6, blocked by default):
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
allow_groups = ["math_core", "algebra", "calculus", "visualization"]

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
| `differentiate` | Symbolic differentiation |
| `plot` | Structured plotting (14 plot types), return PNG image |
| `data_query` | Query built-in curated data (20 sources: country, element, planet, …) |
| `query` | WolframAlpha natural language query (requires `external_services` group) |

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
