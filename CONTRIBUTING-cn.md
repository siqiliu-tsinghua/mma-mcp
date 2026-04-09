# Contributing to mma-mcp

感谢你对本项目的关注！欢迎以下类型的贡献：

## 欢迎贡献的方向

- **其他平台的部署文档**：当前部署指南仅覆盖 Debian/Ubuntu。如果你在 RHEL/Fedora、Arch、macOS、Windows (WSL2) 等平台上成功部署，欢迎提交适配文档或脚本。
- **其他 MCP 客户端的连通验证**：如 Claude Desktop、Cursor、Windsurf 等，欢迎补充连通步骤和已知问题。
- **Bug 修复和功能改进**

## 开发环境

```bash
# 克隆项目
git clone https://github.com/liusq7/mma-mcp.git
cd mma-mcp

# 安装依赖（需要 uv）
uv sync --all-extras

# 运行单元测试（不需要 Wolfram Engine）
uv run pytest tests/ -m "not integration" -q

# 运行集成测试（需要本地 Wolfram Engine）
uv run pytest tests/ -m integration -q
```

## 提交规范

- 提交信息简洁，描述"为什么"而非"改了什么"
- 新功能请附带测试
- 确保 `uv run pytest tests/ -m "not integration" -q` 全部通过后再提交 PR
