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
- [x] ~~Xvfb 虚拟显示~~ 已移除——Wolfram Kernel 的 Export 不需要 DISPLAY/Xvfb，直接落盘渲染

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
- [x] 客户端认证：客户端 ID + 密码（scrypt 哈希，零外部依赖）
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
- [x] 工具精简：删除 solve/simplify/integrate/differentiate/plot/data_query/query，仅保留 evaluate + evaluate_image 两个通用工具

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

- [x] Caddyfile.example（HTTPS + alidns DNS-01 证书）
- [x] .mcp.json（VSCode / Claude Code 本地 stdio 配置）
- [x] systemd service 文件
- [x] 部署文档 / README.md

### 客户端验证

- [ ] Claude Desktop (stdio) 连通测试
- [x] Claude Code (stdio) 连通测试——evaluate（文本）+ evaluate_image（PNG）+ 安全过滤均正常
- [x] Claude.ai (HTTP + Caddy) 远程连通测试——evaluate 文本正常，evaluate_image 图片在折叠的 tool result 中可查看
- [x] ChatGPT (HTTP + Caddy) 远程连通测试——evaluate 文本正常，evaluate_image 图片前端不直接渲染但数据已返回，追问后可转为文件链接

### 跨平台验证

图形渲染已简化为直接 Export 落盘（不依赖 DISPLAY/Xvfb），但以下平台尚未验证：

- [ ] Windows（原生 / WSL2）
- [ ] macOS
- [ ] 其它 Linux 发行版（Ubuntu、RHEL/CentOS、Arch 等）

## Phase 6: 安全与正确性修复

> 基于 2026-04-02 外部评估报告，按优先级排列。

### P0：安全关键

- [x] **白名单模式被静默放宽**：已修正 `registry.py`，缺失组不再回退到全量系统符号，只记 ERROR 日志。补了回归测试。
- [x] **OAuth 客户端注册未校验**：已在 `_authorize_submit()` 加入 client_id 注册校验、redirect_uri 匹配校验、强制 PKCE。补了 HTTP 流程测试。
- [x] **`tools="*"` 只解析到部分工具**：已在 `_build_role_runtimes()` 导入全部 5 个工具模块。补了测试。
- [x] **危险组符号遗漏**：`Evaluate`/`MakeExpression` 加入 `dynamic_eval`，`SetDirectory`/`ResetDirectory` 加入 `file_read`，`SystemShell` 加入 `system_exec`。

### P1：功能正确性

- [x] **会话隔离不一致**：已统一 `simplify()`/`integrate()`/`differentiate()` 的 `context=ctx.session_context` 传递。
- [x] ~~**Xvfb 启动假阳性**~~ 已移除——实测 Wolfram Kernel Export 不需要 DISPLAY/Xvfb
- [x] **WLD enrichment 全损**：改为 batch 级 try/except，失败的 batch 跳过并计数，已完成结果保留。
- [x] ~~**图形检测 Debian 耦合**~~ 已移除——整个 check_graphics/DISPLAY/Xvfb 逻辑已删除

### P2：契约与文档

- [x] `query` 工具的 `format` 参数无实际效果——已移除该参数
- [x] `data_query` 中 `WeatherData`/`FinancialData` 绕过安全分组——已加入 `external_services` 危险种子，本地数据函数加入 `quantitative` 安全种子
- [x] `kernel.wolframscript` 配置项未接入——已删除该配置项和 `find_wolframscript` 函数（从未被使用）

### 待排查

- [x] **`mma-mcp setup` 连接内核后卡住**：根因是 `Attributes[Evaluate@ToExpression[#]]` 对 `$Cloud*` 等符号触发网络连接导致内核死锁。修复：改用 `Attributes[#]` 直接传字符串，不需要 `ToExpression`。全量 setup 约 46 秒完成。
- [x] **危险组分类方法重构完成**：已从通配符+手工枚举改为 WolframLanguageData FunctionalityAreas 驱动分类。208 个 FunctionalityAreas 映射到 22 个安全组 + 6 个危险组，覆盖 6034/7805 符号（77%）。未分类符号主要是 Box/Frontend 内部符号，白名单模式下默认拒绝。危险种子列表作为安全兜底始终包含。
