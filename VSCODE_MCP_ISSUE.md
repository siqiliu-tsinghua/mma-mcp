# VS Code 无法加载 `mma-mcp` 的排查说明

## 现象

- 这个 MCP server 进程可以启动。
- 但在 VS Code 的 Claude Code extension 里加载失败。
- 失败点不是工具函数本身，而是 MCP 的 `stdio` 握手阶段。

## 已确认的结论

- 当前项目使用的是 `mcp` Python SDK，版本是 `1.26.0`。
- 入口脚本在 [src/mma_mcp/server.py](/home/fft/src/mma-mcp/src/mma_mcp/server.py) 里，原本通过 `FastMCP.run()` 走 SDK 自带的 `stdio` transport。
- 用本地最小化测试向 server 发送 MCP `initialize` 请求时，进程会启动，但不会返回初始化响应。
- 同样的问题不只出现在本项目业务代码里，最小版 `FastMCP` 示例也能复现，所以根因更像是当前 SDK / transport 层兼容问题，而不是 Wolfram 工具逻辑。

## 直接原因

怀疑点在 SDK 自带的 `mcp.server.stdio.stdio_server()`。

在当前环境里，它表现为：

- 进程已启动
- 但 pipe 模式下读不到客户端发来的 `stdin` 消息
- 因此无法响应 `initialize`
- VS Code 扩展就会表现成“server 无法加载”

## 建议修复方向

优先考虑下面两种方案之一：

1. 不走 SDK 自带的 `stdio_server()`，改成自定义 `stdio` transport
   - 用 `asyncio` 的 `connect_read_pipe` / `connect_write_pipe`
   - 然后手动调用底层 MCP server 的 `run(...)`

2. 回退或替换 `mcp` SDK 版本
   - 找一个在 VS Code / pipe 模式下能正常完成 `initialize` 的版本
   - 然后恢复使用官方 `FastMCP.run()`

## 建议 Claude 先做的验证

先验证这一步，不要一上来测 Wolfram：

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"1.0.0"}}}' | uv run mma-mcp
```

预期应该马上返回一行 JSON，里面包含：

- `result`
- `protocolVersion`
- `serverInfo`

如果这里没有回包，VS Code 扩展基本就一定加载不了。

## VS Code 侧建议配置

如果 server 本身修好，扩展侧建议至少保证：

- `command` 是 `uv` 或 venv 里的可执行文件
- `args` 为 `["run", "mma-mcp"]`
- `cwd` 指向当前项目目录 `/home/fft/src/mma-mcp`

例如：

```json
{
  "command": "uv",
  "args": ["run", "mma-mcp"],
  "cwd": "/home/fft/src/mma-mcp"
}
```

或者：

```json
{
  "command": "/home/fft/src/mma-mcp/.venv/bin/mma-mcp"
}
```

## 补充说明

- 这个问题看起来主要是 transport 层，不是 Wolfram Kernel 求值逻辑本身。
- 修复完成后，再去验证 `list_tools` 和实际 tool call。
