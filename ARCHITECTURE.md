# mma-mcp 架构文档

## 项目定位

mma-mcp 是一个 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 服务器，使持有 Wolfram Engine / Mathematica 许可证的个人能够通过 AI 助手（Claude、ChatGPT 等）调用自己本地安装的 Wolfram 内核进行符号计算、数值分析和数据可视化。

> **合规声明**：本项目是非官方的个人作品，与 Wolfram Research 无任何隶属、赞助或认证关系。用户须自行合法获取 Wolfram Engine / Mathematica 许可证，并在许可范围内使用。本项目不包含任何 Wolfram 二进制文件、激活密钥或授权文件。

### 设计目标

1. **开箱即用**：单份 TOML 配置文件控制所有行为，`mma-mcp init` 一键生成默认配置。
2. **安全第一**：表达式在到达内核前经过符号级安全过滤，防止通过 MCP 执行系统命令或文件操作。
3. **AI 客户端隔离**：内置 OAuth 2.1 + 角色系统，支持不同 AI 客户端（如 Claude 和 ChatGPT）使用不同策略、互不干扰。
4. **可配置而非可编程**：新增/禁用工具、调整安全策略、管理客户端权限均通过配置完成，不改代码。
5. **依赖精简**：核心运行时仅依赖 `mcp[cli]` + `wolframclient`，密码哈希使用 stdlib。

### 典型应用场景

- **个人研究**：本机 stdio 连接，Claude Desktop / Claude Code 直接调用 Wolfram 求解方程、绘图、符号推导。
- **多客户端隔离**：同一台机器上为不同 AI 客户端配置不同角色，限制各自可用的 Wolfram 功能子集和资源上限。

### 明确不做的事

- **不是 Wolfram Cloud 客户端**——仅使用本地 Wolfram Engine，不联网调用 Wolfram 服务。
- **不是通用代码执行平台**——只执行 Wolfram Language，且受安全策略约束。
- **不做多用户服务**——设计为单一许可证持有者的个人工具，不面向团队或组织共享内核。

---

## 技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| 语言 | Python 3.11+ | stdlib `tomllib`、`hashlib.scrypt`、`contextvars` |
| MCP 协议 | `mcp[cli]` (FastMCP) | 官方 Python SDK，提供 tool/resource/prompt 抽象 |
| Wolfram 桥接 | `wolframclient` | `WolframLanguageSession` 持久连接本地内核 |
| HTTP 服务 | Starlette + uvicorn | FastMCP 的 Streamable HTTP 底层，OAuth 路由直接挂载 |
| 包管理 | uv | 开发和运行均通过 `uv run` |
| TLS 终结 | Caddy（可选） | 自动 HTTPS + DNS-01 证书申请，项目可生成 Caddyfile |

---

## 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                     MCP 客户端                           │
│  Claude Desktop / Claude Code / Claude.ai / ChatGPT     │
└──────────────┬──────────────────────────────────────────┘
               │  stdio 管道 / HTTPS (Streamable HTTP)
               │
┌──────────────▼──────────────────────────────────────────┐
│                    mma-mcp 服务端                        │
│                                                         │
│  ┌─────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ OAuth   │  │ Bearer Auth  │  │ Starlette / stdio  │  │
│  │ Server  │  │ Middleware   │  │ Transport          │  │
│  └────┬────┘  └──────┬───────┘  └────────┬──────────┘  │
│       │              │                    │             │
│       └──────────────┼────────────────────┘             │
│                      │ current_client (contextvar)      │
│              ┌───────▼───────┐                          │
│              │  Tool Router  │  角色权限检查              │
│              │  _safe_wrapper│  _active_filter           │
│              └───────┬───────┘                          │
│                      │                                  │
│         ┌────────────▼────────────┐                     │
│         │   ExpressionFilter      │  AST 符号提取        │
│         │   (per-role or global)  │  + 黑/白名单校验     │
│         └────────────┬────────────┘                     │
│                      │  clean expression                │
│              ┌───────▼───────┐                          │
│              │  KernelPool   │  无状态 worker 池         │
│              │  (pool.py)    │  进程级隔离               │
│              └───────┬───────┘                          │
│                      │  acquire → execute → release     │
└──────────────────────┼──────────────────────────────────┘
                       │
          ┌────────────▼────────────┐
          │ Worker 1 ... Worker N   │  独立 KernelSession
          │ (auto-restart, 定期回收) │
          └────────────┬────────────┘
                       │
               ┌───────▼───────┐
               │ Wolfram Engine │  MathKernel × N
               └───────────────┘
```

---

## 模块职责

### 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| **Server** | `server.py` | `App` 类封装服务器全生命周期；CLI 入口 (`main`)；argparse 子命令；HTTP/stdio 启动 |
| **Config** | `config.py` | TOML 配置加载/校验/默认值生成；所有 dataclass 定义（Kernel/Server/TLS/Security/Tools/Auth/Role/Client） |
| **Kernel** | `kernel.py` | `KernelSession` 管理单个 Wolfram 内核生命周期；自动探测内核路径；崩溃自动重启；Python 侧硬超时 |
| **Pool** | `pool.py` | `KernelPool` 无状态 worker 池；懒创建、独占使用、临时上下文清理、定期重启、空闲回收 |

### 安全模块

| 模块 | 文件 | 职责 |
|------|------|------|
| **Filter** | `security/filter.py` | `ExpressionFilter`：正则提取 WL 符号 → 黑/白名单校验；处理 `Symbol["X"]` 和 `<<` 语法糖 |
| **Registry** | `security/registry.py` | `CapabilityRegistry`：加载分组 JSON → 构建 `ExpressionFilter`；支持多次 `build_filter` 生成不同策略 |
| **Groups** | `security/groups/*.json` | 28 个预生成的符号分组（22 安全 + 6 危险），由 `mma-mcp setup` 基于 WolframLanguageData 生成 |

### 认证模块

| 模块 | 文件 | 职责 |
|------|------|------|
| **Auth** | `auth.py` | `BearerAuthMiddleware`：Bearer token 验证；`ClientIdentity` + `current_client` contextvar 传递客户端身份 |
| **OAuth** | `oauth.py` | 最小 OAuth 2.1 服务器：元数据发现、DCR、Authorization Code + PKCE；多客户端/单密码双模式；token 和 DCR 客户端持久化到 SQLite（WAL 模式） |
| **Passwords** | `passwords.py` | `hash_password` / `verify_password`：stdlib `hashlib.scrypt`，零外部依赖 |

### 工具模块

| 模块 | 文件 | 职责 |
|------|------|------|
| **Registry** | `tools/__init__.py` | `@register` 装饰器 + `_REGISTRY`；`ToolContext` 运行时上下文（含结果截断）；`RoleRuntime` 角色权限；`_safe_wrapper` 错误捕获 + RBAC |
| **Evaluate** | `tools/evaluate.py` | `evaluate`（文本结果）、`evaluate_image`（PNG 图片）——所有 Wolfram Language 功能通过这两个通用工具访问 |

### 辅助模块

| 模块 | 文件 | 职责 |
|------|------|------|
| **Stdio Transport** | `stdio_transport.py` | 自定义 stdio 传输，解决 MCP SDK 在管道环境（VSCode）下的挂起问题 |
| **Caddyfile** | `caddyfile.py` | 根据配置生成 Caddy HTTPS 配置，支持 5 种 DNS 提供商的 DNS-01 证书申请 |
| **Setup** | `setup_groups.py` | 从本地内核查询 WolframLanguageData FunctionalityAreas 重新生成安全分组 JSON |

---

## 关键设计决策

### 1. 前置安全过滤（Python 层解析，内核不参与）

```
输入 → Python 正则提取符号 → 策略校验 → 通过后才发给内核
```

**为什么不在内核内过滤？** Wolfram Language 是图灵完备的，在内核内沙箱化极其困难——`ToExpression`、`Symbol` 等元编程功能可以绕过几乎任何内核级限制。在 Python 层做静态符号分析虽然不完美（无法捕获所有动态构造），但对 AI 生成的表达式足够有效，因为 AI 不会刻意混淆。

**已知局限**：动态字符串拼接构造符号名（如 `ToExpression["Ru" <> "n"]`）无法被静态分析捕获。因此 `ToExpression` 本身被归入 `dynamic_eval` 危险组，默认阻断。

### 2. 无状态 Worker 池（内核进程级隔离）

借鉴 Apache prefork MPM，`KernelPool`（`pool.py`）维护多个独立的 `KernelSession` worker 进程，每次工具调用独占一个 worker，用完清理归还。

```
工具调用 → pool.worker() acquire → 独占 KernelSession
       → 临时上下文 Pool$<random>` 内执行
       → Remove["Pool$...`*"] 清理
       → release 归还池
```

**为什么不用单内核 + context 分区？** `Block[{$Context}]` 只改变默认符号命名空间，不阻止跨 context 访问。恶意客户端可通过 `Contexts[]`/`Names[]` 发现其他客户端的变量，通过 `UpValues` 注入修改其他客户端的函数行为。进程级隔离从根本上消除这一攻击面。

**池行为**：
- **懒创建**：启动时只创建 `pool_min_idle`（默认 1）个 worker，并发请求到来时按需扩到 `pool_size`
- **独占使用**：每次工具调用从池中 acquire 一个空闲 worker，评估期间其他请求不能共享该 worker
- **调用清理**：每次调用用随机临时上下文 `Pool$<hex>`，执行后 `Remove["Pool$...`*"]`
- **定期重启**：worker 处理 `max_requests_per_worker`（默认 100）次后重启内核进程，兜底清理内存膨胀
- **空闲回收**：超过 `pool_min_idle` 的空闲 worker 在 idle timeout 后关闭

**内存实测**：空闲 WolframKernel 进程 RSS 仅 10-20MB，中度使用 ~200MB，重度可达 ~800MB。池大小默认 4（`min(cpu_count, 4)`），空闲状态总开销 < 100MB。

**无状态设计**：AI 客户端天然擅长生成自包含表达式（`Module`/`With`/`Block` 封装局部状态），不需要跨调用变量持久化。

### 3. 配置驱动而非代码驱动

所有行为（传输方式、安全策略、工具启用、客户端权限）均通过 `mma_mcp.toml` 控制。

- **新增工具**：写函数 + `@register` → 配置 `enabled` 列表启用。
- **调整安全**：改 `deny_groups` / `allow_groups`，无需理解过滤器代码。
- **管理客户端**：`mma-mcp add-client` 生成 TOML 片段，粘贴到配置文件。

### 4. OAuth 2.1 + 静态 token 双模式认证

Web MCP 客户端（Claude.ai、ChatGPT）要求标准 OAuth 2.1 流程，不支持自定义 header。因此项目内置了一个最小 OAuth 服务器，同时保留静态 Bearer token 兼容 CLI 客户端。

- **Web 客户端**：标准 OAuth（元数据发现 → DCR → 授权页面 → PKCE token 交换）
- **CLI 客户端**：`Authorization: Bearer base64(client_id:password)`
- **旧模式兼容**：不配 `[auth]` 段时，退化为单密码 + 环境变量

### 5. 角色权限通过 contextvars 传递

`current_client` contextvar 在认证中间件中设置，在工具 wrapper 中读取。每个请求选择对应角色的 `ExpressionFilter`，通过 `_active_filter` contextvar 传递给 `ToolContext.check()`。

- **为什么不直接在 ToolContext 上切换 filter？** 并发请求共享同一个 `ToolContext` 实例，直接修改 `expr_filter` 属性会导致竞态条件。contextvar 是 per-async-task 的，天然并发安全。

### 6. 自定义 stdio 传输

MCP SDK 的默认 stdio transport 在管道环境（VSCode 扩展）下会挂起。项目实现了 `stdio_transport.py`，使用 `asyncio.connect_read_pipe` 和直接 `stdout.buffer` 写入，解决了这个问题。

---

## 安全模型

### 分层防御

```
Layer 1: 认证 (auth.py)
  └─ Bearer token / OAuth → 确认客户端身份

Layer 2: 角色权限 (tools/__init__.py)
  └─ 工具级访问控制 → 角色能调哪些 MCP tool

Layer 3: 表达式过滤 (security/)
  └─ 符号级控制 → 角色的表达式能使用哪些 WL 函数
```

### 符号分组

28 个预定义分组（基于 WolframLanguageData FunctionalityAreas），分为安全和危险两类：

**安全（22 组，默认允许）**：math_core, algebra, calculus, linear_algebra, statistics, number_theory, combinatorics, data_structures, programming, visualization, graph_theory, geometry, optimization, signal_processing, image, machine_learning, chemistry_biology, quantitative, compile, crypto, fractal, interpolation

**危险（6 组，默认阻断）**：system_exec, dynamic_eval, file_write, file_read, networking, external_services

### 两种过滤模式

- **黑名单**（默认）：只阻断危险分组中的符号，其余全部允许。
- **白名单**：只允许指定分组中的符号，其余全部阻断。适合受限环境。

每个角色可独立选择模式和分组，也可继承全局设置。`security = "none"` 跳过过滤（管理员）。

---

## 传输与部署

### 两种传输模式

| 模式 | 启动方式 | 适用场景 |
|------|----------|----------|
| **stdio** | `mma-mcp` 或 `mma-mcp serve` | 本地 MCP 客户端（Claude Desktop、Claude Code、VSCode） |
| **HTTP** | `mma-mcp serve --transport http` | 需要通过 HTTPS 连接的 MCP 客户端 |

### HTTPS 部署架构

```
客户端 → Caddy (TLS 终结, Let's Encrypt) → 127.0.0.1:8000 (mma-mcp HTTP)
```

- Caddy 处理 HTTPS 和证书自动续期（DNS-01 或 HTTP-01）
- mma-mcp 只监听 localhost，由 Caddy 做 TLS 终结
- `mma-mcp caddyfile` 命令根据配置自动生成 Caddyfile

---

## 配置概览

所有配置集中在 `mma_mcp.toml`（`mma-mcp init` 生成），结构如下：

```toml
[kernel]          # 内核路径、超时（WL 侧 + Python 侧硬超时）、结果大小限制、默认输出格式
[server]          # 传输模式、监听地址、旧式单密码认证
[tls]             # HTTPS 域名、DNS 提供商（用于 Caddyfile 生成）
[security]        # 全局安全策略：模式 + 分组 + 符号级覆盖
[tools]           # 启用的 MCP 工具列表
[auth]            # 客户端认证开关
[auth.roles.*]    # 角色定义：工具权限 + 安全策略覆盖
[auth.clients.*]  # 客户端定义：角色绑定 + 密码哈希
```

---

## CLI 命令

| 命令 | 说明 |
|------|------|
| `mma-mcp` / `mma-mcp serve` | 启动 MCP 服务器 |
| `mma-mcp init` | 生成默认 `mma_mcp.toml` |
| `mma-mcp setup` | 从本地内核重新生成安全分组 JSON |
| `mma-mcp caddyfile` | 根据 TLS 配置生成 Caddyfile |
| `mma-mcp hash-password` | 交互式哈希密码 |
| `mma-mcp add-client <id> --role <role>` | 生成客户端 TOML 片段 |

---

## 扩展指南

### 添加新工具

1. 在 `tools/` 下新建模块，用 `@register("tool_name")` 装饰函数：
   ```python
   @register("my_tool")
   def my_tool(ctx: ToolContext, expression: str) -> str:
       ctx.check(expression)  # 安全过滤
       with ctx.pool.worker() as (kernel, wl_context):
           return kernel.evaluate_to_string(expression, ctx.default_format,
                                            timeout=ctx.timeout, context=wl_context)
   ```
2. 在 `tools/__init__.py` 的 `register_tools` 中导入该模块。
3. 在 `mma_mcp.toml` 的 `[tools] enabled` 中添加 `"my_tool"`。

### 添加新安全分组

1. 运行 `mma-mcp setup` 从本地内核重新生成所有分组 JSON。
2. 或手动在 `security/groups/` 下添加 JSON 文件（符号名列表）。
3. 在 `manifest.json` 中添加分组元数据。
4. 分组名自动可用于配置文件中的 `allow_groups` / `deny_groups`。
