# mma-mcp TODO

## P3: 小问题 / 整洁性

- [ ] **无 CI/CD 配置**
  没有 GitHub Actions 等自动化测试。
  修复方向：添加基本的 pytest CI workflow（至少跑非 integration 的单元测试）。

- [x] **CLI 子命令无单元测试**
  `init`、`setup`、`caddyfile`、`hash-password`、`add-client` 五个子命令无测试覆盖。
  已修复：`tests/test_cli.py`，23 个测试覆盖 argparse 解析、各子命令逻辑、main 路由。
