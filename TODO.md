# mma-mcp TODO

## Phase 0: 项目脚手架

- [x] pyproject.toml + uv 依赖管理
- [x] 包结构 `src/mma_mcp/`
- [x] CLAUDE.md 项目规范文档
- [x] .gitignore + git 仓库初始化

## Phase 1: 内核 + 安全 + 最小可用工具

### 内核管理 (kernel.py)

- [x] `KernelSession` 封装 `WolframLanguageSession`
- [x] `evaluate` / `evaluate_to_string` / `evaluate_to_image` 方法
- [x] 内核崩溃自动重启（evaluate 失败重试一次）
- [x] `find_kernel()` 自动探测内核路径（env → which → 常见路径）
- [x] Xvfb 虚拟显示自动启动（无头环境下的图形渲染）

### 安全过滤 (security/)

- [x] `ExpressionFilter`：正则提取符号 + 黑/白名单校验
- [x] `CapabilityRegistry`：加载 JSON 分组、构建策略
- [x] 20 个分组 JSON 文件（14 safe + 6 dangerous）
- [x] `manifest.json` 分组元数据
- [x] `Symbol["Run"]` 动态符号构造检测
- [x] `<<` (Get) 语法糖检测
- [x] `mma-mcp setup` 命令：从本地内核重新生成分组
- [x] Layer 1: pattern + seed 离线分组
- [x] Layer 2: WolframLanguageData 联网分组（可选，优雅降级）
- [x] `scripts/generate_groups.wl` 纯 WL 版分组生成脚本

### MCP 工具

- [x] `evaluate` — 执行 WL 表达式，返回文本（默认 TeXForm）
- [x] `evaluate_image` — 执行 WL 表达式，返回 PNG 图片

### 传输层

- [x] stdio 传输（自定义实现，解决 VSCode 管道问题）
- [x] HTTP (Streamable HTTP) 传输
- [x] `--transport` 命令行参数切换

## Phase 2: 配置驱动架构

> 核心目标：用户通过一份 TOML 配置文件控制服务器行为，无需改代码。

### 2.1 配置系统

- [x] `mma_mcp.toml` 配置文件规范设计
- [x] 配置加载模块 `config.py`：读取 `mma_mcp.toml`（优先）或 `pyproject.toml [tool.mma-mcp]`
- [x] 生成默认配置文件命令（`mma-mcp init`）

### 2.2 工具注册机制

- [x] 工具拆分到 `tools/` 子模块（evaluate.py, math.py）
- [x] 工具注册表：`@register` 装饰器 + `_REGISTRY` 字典
- [x] `[tools] enabled = [...]` 配置项控制哪些工具注册到 MCP server
- [x] server.py 启动时按配置动态注册工具

### 2.3 安全策略配置化

- [x] `[security]` 段读取 mode / deny_groups / allow_groups / extra_*
- [x] 替换现有硬编码的 `_build_security_config()`
- [x] 安全策略与工具注册解耦（两层独立生效）

### 2.4 内核配置

- [x] `[kernel]` 段读取 path / timeout / default_format
- [x] 超时保护：`TimeConstrained` 包装，超时秒数来自配置
- [x] 默认输出格式来自配置

### 2.5 错误处理

- [x] 统一错误捕获（内核异常 / 安全拦截 → 用户友好消息，不崩溃 server）

### 2.6 认证与角色系统

- [x] OAuth 2.1 授权服务器（Metadata + DCR + PKCE + Authorization Code）
- [x] 静态 Bearer token 认证（CLI / 桌面客户端）
- [x] 多用户支持：用户名 + 密码（scrypt 哈希，零外部依赖）
- [x] 角色系统：每个用户绑定角色，每个角色控制可用工具和安全组
- [x] 角色安全策略：`security = "none"` / `"blacklist"` / `"whitelist"` / 继承全局
- [x] `contextvars` 传递用户身份，并发安全
- [x] CLI 认证：`base64(username:password)` Bearer token
- [x] 向后兼容：无 `[auth]` 段时行为不变
- [x] `mma-mcp hash-password` 命令
- [x] `mma-mcp add-user` 命令（生成 TOML 片段）

### 2.7 代码质量

- [x] CLI 统一为 argparse subcommand（serve/init/setup/caddyfile/hash-password/add-user）
- [x] `App` 类封装服务器状态，替代全局单例，利于测试和多实例

## Phase 3: 扩展工具集

> 架构就绪后，新增工具只需：写工具函数 + 加入注册表 + 用户在配置中 enable。

- [x] `solve` — 方程求解
- [x] `simplify` — 表达式化简
- [x] `integrate` — 积分
- [x] `differentiate` — 微分
- [x] `query` — WolframAlpha 风格自然语言查询（需启用 external_services 组）
- [x] `plot` — 语义化绘图入口（14 种图表类型）
- [x] `data_query` — 内置知识库查询（20 种数据源，大部分离线可用）
- [ ] 更多工具按需添加…

## Phase 4: 健壮性

- [x] 会话隔离：WL context 命名空间隔离，每用户独立变量作用域
- [x] 内核健康检查（定期 ping、空闲超时回收，`health_check_interval` / `idle_timeout` 配置项）
- [x] 结果大小限制（`max_result_size` 配置项，超限截断）
- [x] Python 侧硬超时（`hard_timeout` 配置项，内核卡死时强制重启）
- [x] 日志完善（结构化日志 + 每请求 request_id 追踪，JSON 格式可选）

## Phase 5: 部署与集成测试

### 测试

- [x] 安全过滤单元测试（黑名单/白名单、边界情况）— 33 tests
- [x] 工具层 + RBAC 单元测试（mock 内核）— 18 tests
- [x] 认证 + OAuth + 密码哈希单元测试 — 20 tests
- [x] 配置加载 + 校验单元测试 — 31 tests
- [x] 集成测试：真实内核 + MCP 协议端到端 — 17 tests via FastMCP.call_tool

### 部署

- [x] Caddyfile.example（反向代理 + alidns DNS-01 证书）
- [x] .mcp.json（VSCode / Claude Code 本地 stdio 配置）
- [x] systemd service 文件
- [x] 部署文档 / README.md

### 客户端验证

- [ ] Claude Desktop (stdio) 连通测试
- [x] Claude Code (stdio) 连通测试
- [ ] Claude Web / ChatGPT (HTTP + Caddy) 远程连通测试

### 跨平台图形渲染验证

目前图形相关的测试、系统依赖诊断和 `check_graphics()` 逻辑均在 **Debian (Linux x86-64)** 环境下完成。以下平台尚未验证：

- [ ] Windows（原生 / WSL2）——WSL 下之前 Xvfb 方式失败，可能是缺系统依赖，待复测
- [ ] macOS——不需要 Xvfb（有原生 display），但 Qt 插件和依赖路径可能不同
- [ ] 其它 Linux 发行版（Ubuntu、RHEL/CentOS、Arch 等）——包名和库路径可能不同

需要关注的点：
- 系统依赖包名差异（如 `libfontconfig1` 在 RHEL 中是 `fontconfig`）
- `ldd` / `ldconfig` 检测路径在不同发行版中的兼容性
- macOS 下 WolframNB 是否仍需 `QT_QPA_PLATFORM=offscreen`
- `check_graphics()` 中的依赖检测逻辑需适配非 Debian 系包管理器

## Phase 6: 安全与正确性修复

> 基于 2026-04-02 外部评估报告，按优先级排列。

### P0：安全关键

- [ ] **白名单模式被静默放宽**：`_refine_whitelist()` 在拿到内核 `System\`` 符号后，把"所有非危险符号"无条件加入白名单，导致 `allow_groups=["arithmetic"]` 时 `Plot` 等仍可通过。需修正 `registry.py:127-130` 的构建逻辑，只对缺失组做精确回退。补回归测试：白名单内核启动前后行为一致。
- [ ] **OAuth 客户端注册未校验**：`_authorize_submit()` 未校验 `client_id` 是否已注册、`redirect_uri` 是否匹配注册记录。公网部署时授权码可被发到任意地址。需在 `oauth.py:191-232` 加强制校验，并强制 PKCE。补真实 HTTP 流程测试。
- [ ] **`tools="*"` 只解析到部分工具**：`_build_role_runtimes()` 执行时只导入了 `evaluate` 和 `math` 模块，admin 的 `tools="*"` 实际只有 6 个工具（缺 `plot`/`data_query`/`query`）。需在构建前统一导入全部工具模块。补测试。

### P1：功能正确性

- [ ] **会话隔离不一致**：`solve()` 传了 `context=ctx.session_context`，但 `simplify()`/`integrate()`/`differentiate()` 未传。多用户下变量可能互串。统一所有工具的 context 传递。
- [ ] **Xvfb 启动假阳性**：`_start_xvfb()` 等待 lock 文件循环结束后，即使 lock 未出现也返回成功。需确认 lock 存在才返回，并检查进程是否已退出。
- [ ] **WLD enrichment 全损**：`_query_functionality_areas()` 任一 batch 异常则 `return {}`，已完成进度全丢。改为 batch 级 try/except，保留已完成结果。
- [ ] **图形检测 Debian 耦合**：`check_graphics()` 应先检查现有 DISPLAY，再尝试 Xvfb；返回真实模式 `display`/`xvfb`/`none`。包名提示标注为 Debian/Ubuntu 专用。

### P2：契约与文档

- [ ] `query` 工具的 `format` 参数无实际效果——要么实现分支，要么移除参数
- [ ] `data_query` 中 `WeatherData`/`FinancialData` 可能绕过安全分组——确认联网语义后归入危险组或拆分 `live_data` 组
- [ ] `kernel.wolframscript` 配置项未接入 `mma-mcp setup` 流程——删除文档说明或实际接入

### 待排查

- [ ] **`mma-mcp setup` 连接内核后卡住**：运行 `mma-mcp setup` 时进程挂起，`top` 中无 mathkernel 进程。`scripts/test_wld.py` 直连内核则一切正常（WLD 查询 0.6–2.4s/batch）。已将 arithmetic 分组的 Attributes 查询改为纯 WL `Module[...]`，但尚未验证修复是否有效。根因待进一步排查。
