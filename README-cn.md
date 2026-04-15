# mma-mcp

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
git clone https://github.com/<owner>/mma-mcp.git
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
