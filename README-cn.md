# mma-mcp

[English](README.md)

> [!WARNING]
> **本项目已废弃，不再维护。代码保留在 GitHub 上仅供参考。**
>
> **一、Wolfram 15 已自带官方 MCP server。** Mathematica / Wolfram 15 内置 `Wolfram/AgentTools` paclet，可启动本地 stdio MCP server，提供 `WolframLanguageEvaluator`（支持每次调用的 `timeConstraint`、可持久化的 `session`、以及沙箱化的 `Method -> "Local"` 模式）、`WolframContext`、Notebook 读写、`CodeInspector`、`TestReport` 等工具。就本地使用而言，它已经覆盖了 mma-mcp 的大部分功能，而且是在内核内部实现的，比外挂封装更可靠。具体用法请参考官方文档。
>
> **二、MCP 协议和 SDK 已经演进。** 本项目基于 v1 版 Python SDK，使用了 `mcp._mcp_server` 等私有接口和自行实现的 stdio transport，而依赖声明是无上限的 `mcp[cli]>=1.0`。在 `mcp` 2.x 成为默认安装结果之后，全新安装很可能在导入阶段就失败。其中内嵌的 OAuth 2.1 服务器和会话管理部分，与当前 MCP 授权模型已不匹配。
>
> **三、如果你仍要运行它，请注意一个安全问题。** 能力分组的 JSON 文件由本地生成且被 gitignore 排除。**如果没有成功运行过 `mma-mcp setup`，默认黑名单会解析为空集合，表达式过滤将不拦截任何符号**——包括 `Run`、文件读写和网络访问。在这种状态下切勿接入任何不可信输入。
>
> **后续计划：** 我们正在开发另一个体量小得多的工具，专注于**从手机端**通过 Claude 或 ChatGPT，经 HTTPS 访问自己工作站上的 Mathematica / Wolfram Engine。敬请期待。

一个 [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) 服务器，封装本地 **Wolfram Engine**，使 AI 助手（Claude、ChatGPT 等）能够通过 Wolfram Language 进行符号数学、数值分析和数据可视化。

> **免责声明：** 这是一个**非官方的**个人独立项目。
> 它与 Wolfram Research, Inc. **没有**任何隶属、赞助、认证关系。
> "Wolfram"、"Wolfram Language"、"Wolfram Engine"、"Mathematica"
> 及相关标识是 Wolfram Research 的商标。
>
> 本软件**不包含**任何 Wolfram Engine / Mathematica 二进制文件、激活密钥、
> 许可证文件或其他专有材料。用户须依据
> [Wolfram 许可条款](https://www.wolfram.com/legal/)自行获取并合法授权
> Wolfram Engine 或 Mathematica。
>
> 本项目的唯一目的是让**持有许可证的个人**能够通过 AI 助手调用自己本地安装的
> Wolfram 内核，在许可证允许的范围内使用。
> **将 Wolfram Engine 访问权限分发给第三方不是本项目的预期用途，
> 可能违反 Wolfram 的许可条款。**

## 功能特性

- **MCP 工具**：`evaluate`（文本）和 `evaluate_image`（PNG，实验性）——通过两个通用工具访问所有 Wolfram Language 功能
- **传输方式**：stdio（本地）和 Streamable HTTP
- **安全过滤**：内核前表达式过滤，支持黑名单/白名单模式和 29 个能力分组
- **客户端 RBAC**：独立客户端凭据，角色级工具和安全策略控制——隔离同一台机器上的不同 AI 客户端
- **OAuth 2.1**：面向 Web MCP 客户端（Claude.ai、ChatGPT）的授权服务器
- **配置驱动**：单一 TOML 文件控制所有行为

## 前置条件

- Python 3.11+
- [Wolfram Engine](https://www.wolfram.com/engine/) 或 Mathematica（需持有有效许可证）
- [uv](https://docs.astral.sh/uv/) 包管理器

## 快速开始

```bash
# 克隆并安装
git clone https://github.com/siqiliu-tsinghua/mma-mcp.git
cd mma-mcp
uv sync

# 图形导出依赖（仅无头服务器需要——桌面环境已自带）
sudo apt-get install -y libfontconfig1 libgl1 libasound2t64 libxkbcommon0 libegl1

# 生成默认配置
uv run mma-mcp init

# 生成安全分组文件（需要 Wolfram 内核，约 1 分钟）
uv run mma-mcp setup

# 启动服务器（stdio 模式，面向本地 MCP 客户端）
uv run mma-mcp serve
```

## 客户端配置

### Claude Code / VS Code（stdio）

添加到 `.mcp.json`：

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

### Claude Desktop（stdio）

添加到 `claude_desktop_config.json`（Settings -> Developer -> Edit Config）：

```json
{
  "mcpServers": {
    "mma-mcp": {
      "command": "/path/to/mma-mcp/.venv/bin/mma-mcp"
    }
  }
}
```

> macOS/Linux 下配置文件位于 `~/Library/Application Support/Claude/claude_desktop_config.json` 或 `~/.config/Claude/claude_desktop_config.json`。

### HTTP 传输

```bash
uv run mma-mcp serve --transport http --host 127.0.0.1 --port 8000
```

## 配置

所有设置集中在 `mma_mcp.toml`（或 `pyproject.toml` 的 `[tool.mma-mcp]` 下）。

```bash
uv run mma-mcp init  # 生成带注释的 mma_mcp.toml
```

主要配置段：

| 段落 | 说明 |
|------|------|
| `[kernel]` | Wolfram 内核路径、超时、输出格式 |
| `[server]` | 传输模式、监听地址、端口 |
| `[security]` | 黑名单/白名单模式、能力分组 |
| `[tools]` | 启用的 MCP 工具 |
| `[tls]` | HTTPS 域名和 DNS 提供商（Caddy） |
| `[auth]` | 客户端身份和角色权限控制 |

## 安全

表达式在到达 Wolfram 内核**之前**进行过滤。通过正则提取符号，并根据当前策略进行检查。

**黑名单模式**（默认）：阻断危险分组（系统执行、文件 I/O、网络、动态求值）。

**白名单模式**：仅允许显式启用的分组中的符号。

29 个能力分组（22 个安全 + 7 个危险）覆盖约 6000 个 Wolfram Language 符号。从本地内核重新生成：

```bash
uv run mma-mcp setup          # 克隆后必须执行（从本地内核生成）
uv run mma-mcp setup --force   # 强制重新生成（如 Wolfram 版本升级后）
```

## 客户端身份与角色

使用 HTTP 传输时，可以配置独立的客户端凭据和角色，以隔离连接到同一内核的不同 AI 客户端（如 Claude 和 ChatGPT）：

```bash
# 生成密码哈希
uv run mma-mcp hash-password

# 生成新客户端的 TOML 配置片段
uv run mma-mcp add-client alice --role admin
```

每个客户端绑定一个角色，控制其可访问的工具、可使用的 Wolfram 符号和资源限制（超时、结果大小）。并发客户端通过内核 worker 池隔离——每次工具调用运行在独占的内核进程中，使用临时 WL 上下文。

详见 `mma_mcp.toml` 中的 `[auth]` 段配置。

## 开发

```bash
# 运行测试
uv run pytest tests/ -v

# 交互式调试 MCP 工具
uv run mcp dev src/mma_mcp/server.py
```

## CLI 命令

| 命令 | 说明 |
|------|------|
| `mma-mcp serve` | 启动 MCP 服务器（默认） |
| `mma-mcp init` | 生成默认 `mma_mcp.toml` |
| `mma-mcp setup` | 从本地内核生成安全分组 JSON |
| `mma-mcp caddyfile` | 生成 HTTPS Caddyfile |
| `mma-mcp hash-password` | 哈希密码 |
| `mma-mcp add-client` | 生成新 AI 客户端的 TOML 片段 |

## 客户端兼容性

| 客户端 | 长时间计算 | 说明 |
|--------|-----------|------|
| Claude.ai | ✔ 支持 | 发送 `progressToken`，服务器心跳保持连接 |
| ChatGPT | ✘ 可能超时 | 不发送 `progressToken`，有独立于服务器心跳的硬超时（约 60 秒） |
| Claude Desktop / Claude Code | 未测试 | 本地 stdio 传输 |

## 许可证

MIT——仅适用于本仓库中的代码。Wolfram Engine / Mathematica 的使用受 Wolfram Research 自身许可条款约束。
