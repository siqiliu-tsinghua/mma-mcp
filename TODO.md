# mma-mcp TODO

> 基于 2026-04-08 全面代码审查，按优先级排列。

## P0: 安全关键

- [x] **OAuth token/client 内存无限增长 (DoS)**
  `oauth.py` 的 `_access_tokens`、`_auth_codes`、`_clients` 三个字典无上限无主动清理。
  `/oauth/register` 端点无认证限制，攻击者可无限注册客户端耗尽内存。
  修复：添加容量上限（100/1000/200）+ `_evict_expired()` 定期清理 + 超限返回 503。

- [x] **临时文件异常时泄漏**
  `kernel.py` 的 `evaluate_to_image` 中，若 `self.evaluate()` 抛异常（如 KernelTimeout），
  临时 PNG 文件不会被清理。
  修复：用 `try/finally` 确保 `Path(tmp_path).unlink(missing_ok=True)` 始终执行。

## P1: 重要改进

- [x] **过滤器未处理 WL 注释**
  `filter.py` 的 `extract_symbols` 没有剥离 `(* ... *)` 注释，导致注释中出现的符号名
  会被误提取（假阳性，合法代码被拦截）。
  修复：添加 `_strip_comments()` 支持嵌套注释剥离，集成到 `extract_symbols` 流程中。

- [x] **`_refine_whitelist` 无实际效果（死逻辑）**
  `tools/__init__.py` 调用 `registry.initialize_system_symbols()`，但
  `CapabilityRegistry` 中 `_all_system_symbols` 被设置后从未被使用。
  修复：删除 `_refine_whitelist`、`initialize_system_symbols` 和 `get_all_system_symbols`。

- [x] **CLAUDE.md 与实际代码严重漂移**
  项目结构图缺少多个模块，列出了不存在的 `utils.py`。认证/RBAC/OAuth 系统完全未记录。
  修复：完全重写 CLAUDE.md，与实际代码结构对齐。

## P2: 一般改进

- [x] **KernelSession 线程安全**
  `_session` 属性可被主线程和 health-check 线程同时读写，无锁保护。
  修复：加 `threading.Lock` 保护 `_session`；`_stop_health_thread` 中 `join()` 等待线程退出。

- [x] **缺少登录暴力破解保护**
  `_check_credentials` 和 `_try_basic_token` 无失败计数或速率限制。
  修复：添加指数退避锁定（5 次失败后 lockout，2^excess 秒递增，上限 15 分钟）。

- [x] **starlette / uvicorn / anyio 未显式声明依赖**
  `auth.py` 导入 `starlette`，`server.py` 导入 `uvicorn` 和 `anyio`，
  但它们只是 `mcp[cli]` 的传递依赖。
  修复：添加 `[project.optional-dependencies] http` 显式声明。

- [x] **DEPLOY.md 认证模式与新系统不一致**
  部署指南展示旧式单 token 认证，但声称 Claude.ai 会走 OAuth 流程。
  修复：区分模式 A（多客户端 OAuth）和模式 B（静态单 token），补充完整配置步骤。

- [x] **App 懒初始化非线程安全**
  `server.py` 的 `App.kernel` / `App.ctx` / `App.mcp` 属性用简单 `if is None` 检查，
  无锁保护。
  修复：双重检查锁（double-checked locking）+ `threading.Lock`。

## P3: 小问题 / 整洁性

- [x] **pyproject.toml dev 依赖声明重复且版本不一致**
  `[project.optional-dependencies] dev` 和 `[dependency-groups] dev` 两处声明，版本号不一致。
  修复：统一版本号。

- [x] **`.mcp.json` 残留无用 DISPLAY 环境变量**
  `"DISPLAY": ":99"` 是 Xvfb 时代的遗留。
  修复：删除 `"env"` 块。

- [x] **CLI `sys.argv.insert` hack**
  `server.py` 修改全局 `sys.argv`，在测试或嵌入场景可能产生副作用。
  修复：改用本地 `argv` 副本 + `parse_args(argv)`。

- [x] **CLI 命令名不一致：`add-client` vs 文档中的 `add-user`**
  代码实现为 `add-client`，但旧文档/注释中可能残留 `add-user`。
  修复：确认代码中无残留，不需要额外修改。

- [x] **`ParallelNeeds` 可能遗漏于危险组**
  `Needs` 在 `file_read` 中，但 `ParallelNeeds` 不在。
  修复：将 `ParallelNeeds` 加入 `file_read` 危险组。

## 架构演进: 内核 Worker 池（跨客户端隔离）

> 2026-04-09 讨论确定方案。当前单内核 + context 命名空间隔离存在跨客户端攻击面
> （Contexts/Names 可发现其他客户端符号，可篡改/UpValue 注入），需改为进程级隔离。

### 安全动机

当前 `_wrap_context()` 用 `Block[{$Context, $ContextPath}, ...]` 做命名空间分区，
但恶意客户端可以：
1. `Contexts[]` 发现其他客户端的上下文名
2. `Names["MCP$alice`*"]` 列出其他客户端的变量
3. 直接赋值篡改其他客户端变量
4. 通过 UpValues 注入修改其他客户端的函数行为

### 设计方案: 无状态 Worker 池

借鉴 Apache prefork MPM 策略，但适配 Wolfram 内核的特点（启动慢、内存重但空闲时轻）。

**核心原则：每次工具调用独占一个 worker，用完清理归还，不保留跨调用状态。**
AI 客户端天然擅长生成自包含表达式（用 Module/With/Block 封装局部状态），
不需要 REPL 式跨调用变量持久化。

#### 配置项

```toml
[kernel]
pool_size = 4                   # worker 硬上限，默认 min(cpu_count, 4)
pool_min_idle = 1               # 最少保持 1 个热 worker（懒创建其余）
max_requests_per_worker = 100   # 每 100 次请求重启 worker（防内存膨胀）
```

#### 池行为

- **懒创建**：启动时只创建 `pool_min_idle` 个 worker，并发请求到来时按需扩到 `pool_size`
- **独占使用**：每次工具调用从池中 acquire 一个空闲 worker，评估期间其他请求不会共享该 worker
- **调用清理**：每次调用用随机临时上下文 `Tmp$<random>`，执行后 `Remove["Tmp$...`*"]`
- **定期重启**：worker 处理 `max_requests_per_worker` 次后重启内核进程，兜底清理
- **空闲回收**：超过 `pool_min_idle` 的空闲 worker 在 idle timeout 后关闭
- **health check**：保留现有机制，下沉到每个 worker

#### 实际内存开销（本机实测）

空闲 WolframKernel 进程 RSS 仅 10-20MB，中度使用 ~200MB，重度使用可达 ~800MB。
池大小设为 4 时，空闲状态总开销 < 100MB，`max_requests_per_worker` 定期重启防止膨胀。

#### 涉及文件

| 文件 | 改动 |
|------|------|
| 新增 `pool.py` | `KernelPool` 类：worker 生命周期、acquire/release、清理、动态伸缩 |
| `config.py` | 添加 `pool_size`、`pool_min_idle`、`max_requests_per_worker`；废弃 `session_isolation` |
| `kernel.py` | 移除 `_wrap_context()`、`sanitize_context_name()`（不再需要 per-client context） |
| `tools/__init__.py` | `ToolContext` 持有 pool 而非单个 kernel；移除 `session_context` 属性 |
| `tools/evaluate.py` | 通过 pool acquire/release 执行 |
| `server.py` | 构建 `KernelPool` 替代单个 `KernelSession` |
| `tests/` | 新增 pool 单元测试；更新现有测试 |

#### 未来扩展（不在本次范围）

有状态会话（dedicated worker）可作为独立工具实现，仅开放给有授权的角色，
通过 RBAC 控制访问。当前只做无状态池。

---

## P3: 小问题 / 整洁性（剩余）

- [ ] **无 CI/CD 配置**
  没有 GitHub Actions 等自动化测试。
  修复方向：添加基本的 pytest CI workflow（至少跑非 integration 的单元测试）。

- [ ] **CLI 子命令无单元测试**
  `init`、`setup`、`caddyfile`、`hash-password`、`add-client` 五个子命令无测试覆盖。
  修复方向：添加基本的 CLI 测试（至少测试 argparse 解析和 `init` 生成文件）。

## 客户端 / 平台验证

- [ ] Claude Desktop (stdio) 连通测试
- [ ] Windows（原生 / WSL2）验证
- [ ] macOS 验证
- [ ] 其它 Linux 发行版验证
