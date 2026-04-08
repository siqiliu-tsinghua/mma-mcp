# mma-mcp VPS 部署指南

## 前置条件

- Debian VPS，已安装 Mathematica 14.3
- 阿里云域名 + RAM 子账号（AliyunDNSFullAccess 权限）
- 80/443 端口可用

---

## 一、VPS 环境准备

```bash
# 1. 从 bundle 克隆项目
git clone /tmp/mma-mcp.bundle /opt/mma-mcp
cd /opt/mma-mcp

# 2. 安装 uv（如未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc   # 或重新登录

# 3. 安装依赖
uv sync

# 4. 确认内核路径
which WolframKernel
# 或者 ls /usr/local/Wolfram/Mathematica/14.3/Executables/WolframKernel

# 5. 生成安全分组（约 1 分钟，首次运行需要启动内核查询符号分类）
uv run mma-mcp setup
```

---

## 二、域名 DNS

```bash
# 在阿里云 DNS 控制台添加 A 记录：
#   主机记录: mma（或你喜欢的子域名）
#   记录值:   <VPS 公网 IP>
#   TTL:      600

# 验证 DNS 生效
dig mma.yourdomain.com +short
# 应返回 VPS 公网 IP
```

---

## 三、配置 mma-mcp

```bash
cd /opt/mma-mcp

# 生成默认配置
uv run mma-mcp init
```

编辑 `mma_mcp.toml`，修改以下关键项：

```toml
[kernel]
# 如果 which WolframKernel 能找到，留空即可
# 否则填写完整路径：
# mathkernel = "/usr/local/Wolfram/Mathematica/14.3/Executables/WolframKernel"

[server]
transport = "http"
host = "127.0.0.1"
port = 8000

[tls]
enabled = true
domain = "mma.yourdomain.com"
dns_provider = "alidns"
```

### 认证模式选择

mma-mcp 支持两种 HTTP 认证模式，按需选择：

**模式 A：多客户端 OAuth（推荐）**

适用于 Claude.ai、ChatGPT 等支持 OAuth 的 Web 客户端，以及需要多用户/多角色的场景。

```toml
[auth]
enabled = true

[auth.clients.claude]
role = "default"
password_hash = "scrypt:..."   # 用 mma-mcp hash-password 生成

[auth.roles.default]
tools = "*"
security = "inherit"
```

生成客户端配置片段：
```bash
uv run mma-mcp add-client claude --role default
# 按提示输入密码，将输出的 TOML 片段粘贴到 mma_mcp.toml
```

Web 客户端（Claude.ai 等）连接时会走 OAuth 2.1 流程（DCR + PKCE + 授权码），
在登录页面输入 client ID 和密码。

**模式 B：静态单 token（简单场景）**

适用于只有单个客户端、不需要角色区分的场景。不要同时启用 `[auth]`。

```toml
[server]
auth_token_env = "MMA_MCP_AUTH_TOKEN"   # 从环境变量读取 token
```

Web 客户端连接时同样会走 OAuth 流程，但登录页面只显示密码字段（token 即密码）。

生成 Caddyfile：

```bash
uv run mma-mcp caddyfile
# 输出文件: Caddyfile
cat Caddyfile   # 检查内容
```

---

## 四、构建 Caddy（带 alidns 插件）

```bash
# 安装 Go（如未安装）
sudo apt-get install -y golang

# 安装 xcaddy
go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest

# 构建带 alidns 插件的 Caddy
~/go/bin/xcaddy build --with github.com/caddy-dns/alidns

# 安装到系统路径
sudo mv caddy /usr/local/bin/
caddy version
```

---

## 五、准备凭据

**模式 A（多客户端 OAuth）：** 密码已通过 `mma-mcp hash-password` 写入配置文件，
环境变量文件只需要 DNS 相关凭据：

```bash
sudo tee /etc/mma-mcp.env > /dev/null << EOF
ALIDNS_ACCESS_KEY_ID=<你的阿里云AccessKeyID>
ALIDNS_ACCESS_KEY_SECRET=<你的阿里云AccessKeySecret>
EOF
sudo chmod 600 /etc/mma-mcp.env
```

**模式 B（静态单 token）：** 需要额外设置 auth token：

```bash
MMA_MCP_AUTH_TOKEN=$(openssl rand -hex 32)
echo "记住这个 token，Claude Web 配置时需要用:"
echo "$MMA_MCP_AUTH_TOKEN"

sudo tee /etc/mma-mcp.env > /dev/null << EOF
MMA_MCP_AUTH_TOKEN=$MMA_MCP_AUTH_TOKEN
ALIDNS_ACCESS_KEY_ID=<你的阿里云AccessKeyID>
ALIDNS_ACCESS_KEY_SECRET=<你的阿里云AccessKeySecret>
EOF
sudo chmod 600 /etc/mma-mcp.env
```

---

## 六、创建专用服务用户

```bash
# 创建无登录 shell 的系统用户
sudo useradd -r -s /usr/sbin/nologin -m -d /opt/mma-mcp mma

# 将项目目录所有权交给该用户
sudo chown -R mma:mma /opt/mma-mcp

# 让 mma 用户可以读取 Mathematica license
# （根据实际安装路径调整，通常 /usr/local/Wolfram 已经是 world-readable）
ls -la /usr/local/Wolfram/Mathematica/14.3/Configuration/Licensing/
```

> **说明：** 绑定低端口（<1024，如 443）是 Linux capability（`CAP_NET_BIND_SERVICE`），
> 不是传统的组权限，无法通过加组解决。下面在 Caddy 的 systemd 服务中通过
> `AmbientCapabilities` 授予，仅对该服务进程生效，不影响全局。

---

## 七、systemd 服务

### mma-mcp 服务

```bash
sudo tee /etc/systemd/system/mma-mcp.service > /dev/null << 'EOF'
[Unit]
Description=mma-mcp Wolfram Engine MCP Server
After=network.target

[Service]
User=mma
Group=mma
WorkingDirectory=/opt/mma-mcp
EnvironmentFile=/etc/mma-mcp.env
ExecStart=/opt/mma-mcp/.venv/bin/mma-mcp serve --transport http --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

### Caddy 服务

```bash
sudo tee /etc/systemd/system/caddy-mma.service > /dev/null << 'EOF'
[Unit]
Description=Caddy reverse proxy for mma-mcp
After=network.target mma-mcp.service

[Service]
User=mma
Group=mma
WorkingDirectory=/opt/mma-mcp
EnvironmentFile=/etc/mma-mcp.env
ExecStart=/usr/local/bin/caddy run --config /opt/mma-mcp/Caddyfile
Restart=on-failure
RestartSec=5

# 允许绑定 443 端口（无需 root）
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
EOF
```

### 启动服务

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mma-mcp caddy-mma

# 检查状态
sudo systemctl status mma-mcp
sudo systemctl status caddy-mma

# 查看日志
sudo journalctl -u mma-mcp -f
sudo journalctl -u caddy-mma -f
```

---

## 八、验证部署

```bash
# 检查 OAuth 元数据端点（不需要认证）
curl -s https://mma.yourdomain.com/.well-known/oauth-authorization-server | python3 -m json.tool
# 应返回包含 authorization_endpoint、token_endpoint 等字段的 JSON

# 检查 MCP 端点（需要认证，预期返回 401）
curl -v https://mma.yourdomain.com/mcp
# 应返回 401 Unauthorized，说明认证中间件正常工作

# 模式 B 用户可直接带 token 测试：
# source /etc/mma-mcp.env
# curl -v -H "Authorization: Bearer $MMA_MCP_AUTH_TOKEN" https://mma.yourdomain.com/mcp
# 应返回 405 Method Not Allowed（MCP 端点不接受 GET，说明服务正常）

# 如果证书未签发，检查 Caddy 日志：
sudo journalctl -u caddy-mma --no-pager | tail -30
```

---

## 九、Claude Web 连通测试

1. 打开 https://claude.ai
2. Settings -> MCP Servers -> Add Server
3. URL: `https://mma.yourdomain.com/mcp`
4. 会走 OAuth 2.1 认证流程：
   - 模式 A：输入 client ID（如 `claude`）和密码
   - 模式 B：仅输入密码（即 auth token）
5. 测试对话：
   - "计算 1+1"
   - "画出 Sin[x] 在 0 到 2π 的图像"
   - "求解 x^2 - 5x + 6 = 0"

---

## 排障

```bash
# mma-mcp 启动失败
sudo journalctl -u mma-mcp -e

# Caddy 证书签发失败
# - 检查 DNS API 凭据是否正确
# - 检查 A 记录是否已生效: dig mma.yourdomain.com
# - 检查环境变量: sudo cat /etc/mma-mcp.env

# 内核找不到
uv run python -c "from mma_mcp.kernel import find_kernel; print(find_kernel())"

# 手动测试 HTTP 端
uv run mma-mcp serve --transport http --host 127.0.0.1 --port 8000
# 另一个终端:
curl http://127.0.0.1:8000/mcp
```
