# mma-mcp Architecture

## Project Purpose

mma-mcp is a [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that enables individual Wolfram Engine / Mathematica license holders to invoke their locally-installed Wolfram kernel through AI assistants (Claude, ChatGPT, etc.) for symbolic computation, numerical analysis, and data visualization.

> **Disclaimer:** This is an unofficial, independent project with no affiliation to Wolfram Research. Users must independently obtain and license their own Wolfram Engine / Mathematica. This project contains no Wolfram binaries, activation keys, or license files.

### Design Goals

1. **Works out of the box**: A single TOML config file controls all behavior; `mma-mcp init` generates defaults.
2. **Security first**: Expressions are filtered at the symbol level before reaching the kernel, preventing system command execution or file operations through MCP.
3. **AI client isolation**: Built-in OAuth 2.1 + role system allows different AI clients (e.g., Claude and ChatGPT) to use different policies without interference.
4. **Configurable, not programmable**: Adding/disabling tools, adjusting security policies, and managing client permissions are all done through configuration, not code changes.
5. **Minimal dependencies**: Core runtime depends only on `mcp[cli]` + `wolframclient`; password hashing uses stdlib.

### Typical Use Cases

- **Personal research**: Local stdio connection, Claude Desktop / Claude Code directly invoking Wolfram to solve equations, plot, and perform symbolic derivation.
- **Multi-client isolation**: Different AI clients on the same machine configured with different roles, restricting each client's available Wolfram function subset and resource limits.

### Explicit Non-Goals

- **Not a Wolfram Cloud client** — uses only the local Wolfram Engine, no network calls to Wolfram services.
- **Not a general-purpose code execution platform** — only executes Wolfram Language, subject to security policy constraints.
- **Not a multi-user service** — designed as a personal tool for a single license holder, not for team or organizational kernel sharing.

---

## Tech Stack

| Layer | Technology | Notes |
|-------|------------|-------|
| Language | Python 3.11+ | stdlib `tomllib`, `hashlib.scrypt`, `contextvars` |
| MCP Protocol | `mcp[cli]` (FastMCP) | Official Python SDK, provides tool/resource/prompt abstractions |
| Wolfram Bridge | `wolframclient` | `WolframLanguageSession` persistent connection to local kernel |
| HTTP Server | Starlette + uvicorn | FastMCP's Streamable HTTP backend, OAuth routes mounted directly |
| Package Manager | uv | Development and runtime both via `uv run` |
| TLS Termination | Caddy (optional) | Auto HTTPS + DNS-01 certificate acquisition; project can generate Caddyfile |

---

## Overall Architecture

```
+-----------------------------------------------------------+
|                      MCP Clients                          |
|  Claude Desktop / Claude Code / Claude.ai / ChatGPT      |
+--------------+--------------------------------------------+
               |  stdio pipe / HTTPS (Streamable HTTP)
               |
+--------------v--------------------------------------------+
|                     mma-mcp Server                        |
|                                                           |
|  +---------+  +--------------+  +-------------------+    |
|  | OAuth   |  | Bearer Auth  |  | Starlette / stdio |    |
|  | Server  |  | Middleware   |  | Transport         |    |
|  +----+----+  +------+-------+  +--------+----------+    |
|       |              |                    |               |
|       +--------------+--------------------+               |
|                      | current_client (contextvar)        |
|              +-------v-------+                            |
|              |  Tool Router  |  Role permission check     |
|              |  _safe_wrapper|  _active_filter             |
|              +-------+-------+                            |
|                      |                                    |
|         +------------v------------+                       |
|         |   ExpressionFilter      |  Symbol extraction    |
|         |   (per-role or global)  |  + blacklist/whitelist |
|         +------------+-----------+                        |
|                      |  clean expression                  |
|              +-------v-------+                            |
|              |  KernelPool   |  Worker pool               |
|              |  (pool.py)    |  Process-level isolation    |
|              +-------+-------+                            |
|                      |  acquire -> execute -> release     |
+----------------------+------------------------------------+
                       |
          +------------v------------+
          | Worker 1 ... Worker N   |  Independent KernelSession
          | (auto-restart, reclaim) |
          +------------+------------+
                       |
               +-------v-------+
               | Wolfram Engine |  MathKernel x N
               +---------------+
```

---

## Module Responsibilities

### Core Modules

| Module | File | Responsibility |
|--------|------|----------------|
| **Server** | `server.py` | `App` class managing server lifecycle; CLI entry point (`main`); argparse subcommands; HTTP/stdio startup |
| **Config** | `config.py` | TOML config loading/validation/default generation; all dataclass definitions (Kernel/Server/TLS/Security/Tools/Auth/Role/Client) |
| **Kernel** | `kernel.py` | `KernelSession` managing a single Wolfram kernel lifecycle; auto-detect kernel path; crash auto-restart; Python-side hard timeout |
| **Pool** | `pool.py` | `KernelPool` worker pool; lazy creation, exclusive use, temporary context cleanup, periodic restart, idle reclaim; process-level isolation |

### Security Modules

| Module | File | Responsibility |
|--------|------|----------------|
| **Filter** | `security/filter.py` | `ExpressionFilter`: regex symbol extraction -> blacklist/whitelist check; handles `Symbol["X"]` and `<<` syntax |
| **Registry** | `security/registry.py` | `CapabilityRegistry`: loads group JSONs -> builds `ExpressionFilter`; supports multiple `build_filter` calls for different policies |
| **Groups** | `security/groups/*.json` | 29 pre-generated symbol groups (22 safe + 7 dangerous), generated by `mma-mcp setup` from WolframLanguageData |

### Authentication Modules

| Module | File | Responsibility |
|--------|------|----------------|
| **Auth** | `auth.py` | `BearerAuthMiddleware`: Bearer token verification; `ClientIdentity` + `current_client` contextvar for client identity propagation |
| **OAuth** | `oauth.py` | Minimal OAuth 2.1 server: metadata discovery, DCR, Authorization Code + PKCE; multi-client/single-password dual mode; tokens and DCR clients persisted to SQLite (WAL mode) |
| **Passwords** | `passwords.py` | `hash_password` / `verify_password`: stdlib `hashlib.scrypt`, zero external dependencies |

### Tool Modules

| Module | File | Responsibility |
|--------|------|----------------|
| **Registry** | `tools/__init__.py` | `@register` decorator + `_REGISTRY`; `ToolContext` runtime context (with result truncation); `RoleRuntime` role permissions; `_safe_wrapper` error handling + RBAC |
| **Evaluate** | `tools/evaluate.py` | `evaluate` (text result), `evaluate_image` (PNG image) — all Wolfram Language capabilities accessed through these two universal tools |

### Auxiliary Modules

| Module | File | Responsibility |
|--------|------|----------------|
| **Stdio Transport** | `stdio_transport.py` | Custom stdio transport, fixes MCP SDK pipe hang in VSCode environments |
| **Caddyfile** | `caddyfile.py` | Generates Caddy HTTPS config from settings; supports 5 DNS providers for DNS-01 certificate acquisition |
| **Setup** | `setup_groups.py` | Queries WolframLanguageData FunctionalityAreas from local kernel to regenerate security group JSONs |

---

## Key Design Decisions

### 1. Pre-Kernel Security Filtering (Python-layer parsing, kernel not involved)

```
Input -> Python regex symbol extraction -> Policy check -> Only passes to kernel if approved
```

**Why not filter inside the kernel?** Wolfram Language is Turing-complete, making kernel-level sandboxing extremely difficult — metaprogramming features like `ToExpression` and `Symbol` can bypass almost any in-kernel restriction. Python-layer static symbol analysis, while imperfect (cannot catch all dynamic constructions), is effective enough for AI-generated expressions, since AI assistants don't deliberately obfuscate.

**Known limitation**: Dynamic string concatenation to construct symbol names (e.g., `ToExpression["Ru" <> "n"]`) cannot be caught by static analysis. Therefore `ToExpression` itself is included in the `dynamic_eval` dangerous group, blocked by default.

### 2. Worker Pool (Kernel Process-Level Isolation)

Inspired by Apache prefork MPM, `KernelPool` (`pool.py`) maintains multiple independent `KernelSession` worker processes. Each tool call exclusively acquires a worker, uses it, cleans up, and returns it.

```
Tool call -> pool.worker() acquire -> exclusive KernelSession
          -> execute in temporary context Pool$<random>`
          -> Remove["Pool$...`*"] cleanup
          -> release back to pool
```

**Why not single kernel + context partitioning?** `Block[{$Context}]` only changes the default symbol namespace; it doesn't prevent cross-context access. Process-level isolation fundamentally eliminates the cross-client symbol space attack surface.

**Pool behavior**:
- **Lazy creation**: starts with `pool_min_idle` (default 1) workers, scales up to `pool_size` on demand
- **Exclusive use**: each tool call acquires an idle worker; no sharing during evaluation
- **Per-call cleanup**: each call uses a random temporary context `Pool$<hex>`, cleaned up with `Remove["Pool$...`*"]` after execution
- **Periodic restart**: workers restart after `max_requests_per_worker` (default 100) evaluations, fully resetting all kernel state
- **Idle reclaim**: workers beyond `pool_min_idle` are shut down after idle timeout

**Memory profile**: idle WolframKernel process RSS is only 10-20MB, moderate use ~200MB, heavy use up to ~800MB. Default pool size is 4 (`min(cpu_count, 4)`), idle overhead < 100MB total.

**Isolation boundary**: Temporary context cleanup covers user-defined symbols. `System`` level state mutations (e.g., `SetOptions`, `Unprotect`) are blocked by the security filter's `system_mutation` dangerous group at the front end; `max_requests_per_worker` periodic restart serves as a backstop.

### 3. Config-Driven, Not Code-Driven

All behavior (transport mode, security policy, tool enablement, client permissions) is controlled via `mma_mcp.toml`.

- **Adding tools**: write a function + `@register` -> add to `enabled` list in config.
- **Adjusting security**: change `deny_groups` / `allow_groups`, no need to understand filter code.
- **Managing clients**: `mma-mcp add-client` generates a TOML snippet, paste into config file.

### 4. OAuth 2.1 + Static Token Dual-Mode Authentication

Web MCP clients (Claude.ai, ChatGPT) require standard OAuth 2.1 flows and don't support custom headers. The project includes a minimal OAuth server while retaining static Bearer token compatibility for CLI clients.

- **Web clients**: standard OAuth (metadata discovery -> DCR -> login page -> PKCE token exchange)
- **CLI clients**: `Authorization: Bearer base64(client_id:password)`
- **Legacy compatibility**: without `[auth]` section, falls back to single password + environment variable

### 5. Role Permissions via contextvars

`current_client` contextvar is set in the auth middleware and read in the tool wrapper. Each request selects the corresponding role's `ExpressionFilter`, passed to `ToolContext.check()` via the `_active_filter` contextvar.

- **Why not switch filter directly on ToolContext?** Concurrent requests share the same `ToolContext` instance. Directly modifying `expr_filter` would cause race conditions. contextvars are per-async-task, naturally concurrent-safe.

### 6. Custom stdio Transport

The MCP SDK's default stdio transport hangs in pipe environments (VSCode extensions). The project implements `stdio_transport.py` using `asyncio.connect_read_pipe` and direct `stdout.buffer` writes to solve this.

---

## Security Model

### Layered Defense

```
Layer 1: Authentication (auth.py)
  +-- Bearer token / OAuth -> confirm client identity

Layer 2: Role Permissions (tools/__init__.py)
  +-- Tool-level access control -> which MCP tools a role can call

Layer 3: Expression Filtering (security/)
  +-- Symbol-level control -> which WL functions a role's expressions can use
```

### Symbol Groups

29 predefined groups (derived from WolframLanguageData FunctionalityAreas), divided into safe and dangerous:

**Safe (22 groups, allowed by default)**: math_core, algebra, calculus, linear_algebra, statistics, number_theory, combinatorics, data_structures, programming, visualization, graph_theory, geometry, optimization, signal_processing, image, machine_learning, chemistry_biology, quantitative, compile, crypto, fractal, interpolation

**Dangerous (7 groups, blocked by default)**: system_exec, dynamic_eval, file_write, file_read, networking, external_services, system_mutation

### Two Filtering Modes

- **Blacklist (default)**: blocks only symbols in dangerous groups, allows everything else.
- **Whitelist**: allows only symbols in specified groups, blocks everything else. For restricted environments.

Each role can independently choose mode and groups, or inherit the global setting. `security = "none"` skips filtering (admin).

---

## Transport & Deployment

### Two Transport Modes

| Mode | Command | Use Case |
|------|---------|----------|
| **stdio** | `mma-mcp` or `mma-mcp serve` | Local MCP clients (Claude Desktop, Claude Code, VSCode) |
| **HTTP** | `mma-mcp serve --transport http` | MCP clients connecting via HTTPS |

### HTTPS Deployment Architecture

```
Client -> Caddy (TLS termination, Let's Encrypt) -> 127.0.0.1:8000 (mma-mcp HTTP)
```

- Caddy handles HTTPS and automatic certificate renewal (DNS-01 or HTTP-01)
- mma-mcp only listens on localhost, Caddy handles TLS termination
- `mma-mcp caddyfile` command auto-generates Caddyfile from config

---

## Configuration Overview

All configuration is centralized in `mma_mcp.toml` (generated by `mma-mcp init`):

```toml
[kernel]          # Kernel path, timeout (WL-side + Python-side hard timeout), result size limit, default output format
[server]          # Transport mode, listen address, legacy single-password auth
[tls]             # HTTPS domain, DNS provider (for Caddyfile generation)
[security]        # Global security policy: mode + groups + per-symbol overrides
[tools]           # Enabled MCP tools list
[auth]            # Client authentication toggle
[auth.roles.*]    # Role definitions: tool permissions + security policy override
[auth.clients.*]  # Client definitions: role binding + password hash
```

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `mma-mcp` / `mma-mcp serve` | Start the MCP server |
| `mma-mcp init` | Generate default `mma_mcp.toml` |
| `mma-mcp setup` | Regenerate security group JSONs from local kernel |
| `mma-mcp caddyfile` | Generate Caddyfile from TLS config |
| `mma-mcp hash-password` | Interactively hash a password |
| `mma-mcp add-client <id> --role <role>` | Generate client TOML snippet |

---

## Extension Guide

### Adding a New Tool

1. Create a new module in `tools/`, decorate the function with `@register("tool_name")`:
   ```python
   @register("my_tool")
   def my_tool(ctx: ToolContext, expression: str) -> str:
       ctx.check(expression)  # Security filtering
       with ctx.pool.worker() as (kernel, wl_context):
           return kernel.evaluate_to_string(expression, ctx.default_format,
                                            timeout=ctx.timeout, context=wl_context)
   ```
2. Import the module in `tools/__init__.py`'s `register_tools`.
3. Add `"my_tool"` to `[tools] enabled` in `mma_mcp.toml`.

### Adding a New Security Group

1. Run `mma-mcp setup` to regenerate all group JSONs from the local kernel.
2. Or manually add a JSON file (list of symbol names) in `security/groups/`.
3. Add group metadata in `manifest.json`.
4. The group name is automatically available for `allow_groups` / `deny_groups` in config.
