# 安全策略

## 范围

本策略覆盖 **mma-mcp** 自身代码（表达式过滤、认证、OAuth、RBAC）。Wolfram Engine / Mathematica 内核的问题不在范围内，请向 [Wolfram Research](https://www.wolfram.com/support/) 报告。

## 支持的版本

| 版本 | 是否支持 |
|------|----------|
| 0.1.x | 是      |

## 报告漏洞

如果你发现 mma-mcp 的安全漏洞，**请不要创建公开 issue**。请：

1. 发邮件给**仓库所有者**，描述漏洞详情、复现步骤和潜在影响。
2. 你将在 72 小时内收到确认。
3. 修复将在私下开发并以补丁形式发布，之后再公开披露。

你也可以使用 GitHub 的[私密漏洞报告功能](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)。

## 安全架构

mma-mcp 采用三层纵深防御模型：

### 第一层：认证（`auth.py` / `oauth.py`）

- **多客户端 OAuth 2.1**：DCR + PKCE + 授权码模式，面向 Web MCP 客户端
- **旧式 Bearer token**：从环境变量读取的静态 token
- **密码哈希**：stdlib `hashlib.scrypt`（N=16384, r=8, p=1），timing-safe 验证
- **暴力破解防护**：指数退避锁定（最长 15 分钟）+ 可选 fail2ban IP 级封禁

### 第二层：角色权限控制（`tools/__init__.py`）

- 角色级工具权限（客户端能调用哪些 MCP 工具）
- 角色级资源限制（超时、结果大小）
- 通过 `current_client` contextvar 强制执行，并发安全

### 第三层：表达式过滤（`security/`）

- **前置过滤**：表达式在到达 Wolfram 内核之前在 Python 层分析
- **符号提取**：多遍正则分词器，处理 `Symbol["X"]`、上下文限定名（`System`Run`）、`<<`（Get）运算符、字符串字面量和注释
- **29 个能力分组**：22 个安全组（数学、可视化等）+ 7 个危险组（system_exec、file I/O、networking、dynamic_eval、system_mutation）
- **两种模式**：黑名单（默认，阻断危险组）和白名单（仅允许指定组）

### 已知局限

- **动态字符串拼接**：`ToExpression["Ru" <> "n"]` 无法被静态检测。已通过在 `dynamic_eval` 组中阻断 `ToExpression` 缓解。
- **内核级状态修改**：`system_mutation` 组阻断 `SetOptions`、`Unprotect` 等，定期 worker 重启（`max_requests_per_worker`）作为兜底。

### 代理头信任

`x-forwarded-proto` 和 `x-forwarded-host` 仅在请求来自回环地址（`127.0.0.1` / `::1`）时才被信任，防止服务器直接暴露时的伪造攻击。

## 推荐部署实践

- mma-mcp 绑定 `127.0.0.1`，使用反向代理（Caddy）做 TLS 终结
- HTTP 传输下启用 `[auth]` 并配置独立客户端凭据
- 使用 fail2ban 做 IP 级暴力破解防护（详见 [DEPLOY-cn.md](DEPLOY-cn.md)）
- Wolfram Engine 升级后重新生成安全分组：`mma-mcp setup --force`
- 审查默认的 `deny_groups` 列表，根据需要调整
