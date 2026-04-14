# mma-mcp HTTPS 部署指南

> **使用场景：** 本指南面向已持有 Wolfram Engine / Mathematica 许可证的个人用户，帮助你将安装了 Wolfram 的机器通过 HTTPS 连接到自己的网页版和移动版 AI 客户端（如 Claude.ai、ChatGPT），以便在任何设备上使用 Wolfram 计算能力。部署后的服务仅供许可证持有者本人使用。是否可用于其他场景，请自行根据你的 Wolfram 许可证条款确认。

> **适用平台：** 本指南针对 **Debian / Ubuntu** 系 Linux。其他发行版的包名和 systemd 细节可能不同，欢迎贡献适配文档（见 [CONTRIBUTING-cn.md](CONTRIBUTING-cn.md)）。

## 前置条件

- Debian / Ubuntu 服务器，已持有并安装 Wolfram Engine / Mathematica
- 图形导出依赖库（无头服务器上 `evaluate_image` 必需）：
  ```bash
  sudo apt-get install -y libfontconfig1 libgl1 libasound2t64 libxkbcommon0 libegl1
  ```
- 一个指向该服务器公网 IP 的域名
- 443 端口可用（使用 HTTP-01 验证时还需要 80 端口）

---

## 一、环境准备

```bash
# 1. 克隆项目并移至 /opt
git clone https://github.com/<owner>/mma-mcp.git
sudo mv mma-mcp /opt/mma-mcp
cd /opt/mma-mcp

# 2. 安装 uv（如未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc   # 或重新登录

# 3. 安装依赖（venv 路径将使用 /opt/mma-mcp）
uv sync

# 4. 确认内核路径
which WolframKernel

# 5. 生成安全分组（约 1 分钟，首次运行需要启动内核查询符号分类）
uv run mma-mcp setup
```

---

## 二、域名 DNS

在你的域名服务商控制台添加 A 记录，将子域名指向服务器公网 IP：

```bash
# 示例：
#   主机记录: mma（或你喜欢的子域名）
#   记录值:   <服务器公网 IP>
#   TTL:      600

# 验证 DNS 生效
dig mma.<your-domain> +short
# 应返回服务器公网 IP
```

---

## 三、配置 mma-mcp

```bash
# 生成默认配置
uv run mma-mcp init
```

编辑 `mma_mcp.toml`，修改以下关键项：

```toml
[kernel]
# 如果 which WolframKernel 能找到，留空即可
# 否则填写完整路径：
# mathkernel = "/path/to/WolframKernel"

[server]
transport = "http"
host = "127.0.0.1"
port = 8000

[tls]
enabled = true
domain = "mma.<your-domain>"
# dns_provider = ""  # 详见下方"TLS 证书"段落
```

### TLS 证书

Caddy 自动获取 Let's Encrypt 证书，支持两种 ACME 验证方式：

**HTTP-01 验证（最简单）**

不需要 DNS 服务商 API，Caddy 通过 80 端口验证域名所有权。`dns_provider` 留空即可：

```toml
[tls]
enabled = true
domain = "mma.<your-domain>"
# dns_provider 不设置 → 使用 HTTP-01 验证（需要 80 端口开放）
```

不需要自定义构建 Caddy，直接安装官方版本即可。跳到下方"生成 Caddyfile"。

**DNS-01 验证（不需要 80 端口）**

Caddy 通过 DNS API 验证域名所有权。适用于 80 端口不可用或需要通配符证书的场景。需要带 DNS 插件的自定义 Caddy 构建。

支持的 DNS 服务商：

| 服务商 | `dns_provider` 值 | 环境变量 |
|--------|-------------------|---------|
| 阿里云 DNS | `alidns` | `ALIDNS_ACCESS_KEY_ID`, `ALIDNS_ACCESS_KEY_SECRET` |
| Cloudflare | `cloudflare` | `CLOUDFLARE_API_TOKEN` |
| DNSPod（腾讯云） | `dnspod` | `DNSPOD_API_TOKEN` |
| GoDaddy | `godaddy` | `GODADDY_API_KEY`, `GODADDY_API_SECRET` |
| Namecheap | `namecheap` | `NAMECHEAP_API_KEY`, `NAMECHEAP_API_USER` |

以阿里云 DNS 为例：

```toml
[tls]
enabled = true
domain = "mma.<your-domain>"
dns_provider = "alidns"
```

### 生成 Caddyfile

```bash
uv run mma-mcp caddyfile
cat Caddyfile   # 检查内容
```

### 认证模式选择

mma-mcp 支持两种 HTTP 认证模式，按需选择：

**模式 A：多客户端 OAuth（推荐）**

适用于同时从多个 AI 客户端（如 Claude.ai 和 ChatGPT）访问同一台机器上的 Wolfram 内核。每个客户端有独立的凭据和可选的权限策略。

第 1 步：取消 `mma_mcp.toml` 中 `[auth]` 和 `[auth.roles.default]` 的注释（`mma-mcp init` 已预生成为注释）：

```toml
[auth]
enabled = true

[auth.roles.default]
tools = "*"
security = ""
```

第 2 步：生成客户端凭据：

```bash
uv run mma-mcp add-client claude --role default
# 按提示输入密码，将生成的 TOML 片段粘贴到 mma_mcp.toml

# 如需添加更多客户端：
uv run mma-mcp add-client chatgpt --role default
```

Web 客户端（Claude.ai 等）连接时会走 OAuth 2.1 流程（DCR + PKCE + 授权码），在登录页面输入 client ID 和密码。

**模式 B：静态单 token（简单场景）**

适用于只有单个客户端、不需要角色区分的场景。不要同时启用 `[auth]`。

```toml
[server]
auth_token_env = "MMA_MCP_AUTH_TOKEN"   # 从环境变量读取 token
```

Web 客户端连接时同样会走 OAuth 流程，但登录页面只显示密码字段（token 即密码）。

---

## 四、创建服务用户

创建专用运行时用户（类似 Apache 的 `www-data`）。`mma` 用户仅用于运行服务——无需 shell、home 目录或任何工具。

```bash
sudo useradd -r -s /usr/sbin/nologin mma
sudo chown -R mma:mma /opt/mma-mcp
```

> **说明：** 绑定低端口（<1024，如 443）是 Linux capability（`CAP_NET_BIND_SERVICE`），不是传统的组权限。下面在 Caddy 的 systemd 服务中通过 `AmbientCapabilities` 授予。

---

## 五、安装 Caddy

**HTTP-01（官方 Caddy）：**

```bash
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy
```

**DNS-01（自定义构建，带 DNS 插件）：**

```bash
# 安装 Go（如未安装）
sudo apt-get install -y golang

# 安装 xcaddy 到系统路径（保留以便将来重新构建）
export GOPATH=/usr/local/share/go
sudo GOPATH=$GOPATH go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest

# 构建带 DNS 插件的 Caddy（以阿里云为例）
sudo $GOPATH/bin/xcaddy build --with github.com/caddy-dns/alidns
# 其他服务商:
#   --with github.com/caddy-dns/cloudflare
#   --with github.com/caddy-dns/dnspod
#   --with github.com/caddy-dns/godaddy
#   --with github.com/caddy-dns/namecheap

# 安装到系统路径
sudo mv caddy /usr/local/bin/
caddy version
```

---

## 六、准备凭据

环境变量文件包含 systemd 服务启动时读取的密钥。

```bash
sudo touch /etc/default/mma-mcp
sudo chmod 600 /etc/default/mma-mcp
sudo editor /etc/default/mma-mcp
```

**使用 DNS-01 时**，填入 DNS 服务商的 API 凭据（将 `<...>` 替换为你的实际密钥）：

```
# 示例：阿里云 DNS
ALIDNS_ACCESS_KEY_ID=<你的AccessKeyID>
ALIDNS_ACCESS_KEY_SECRET=<你的AccessKeySecret>
```

其他服务商请填入对应的环境变量（见第三节的表格）。

**使用 HTTP-01 时**，文件可以为空（或仅包含下面的认证凭据）。

**使用静态单 token 认证（模式 B）时**，添加 token：

```bash
# 生成随机 token
openssl rand -hex 32
```

复制输出，添加到 `/etc/default/mma-mcp`：

```
MMA_MCP_AUTH_TOKEN=<粘贴token>
```

---

## 七、systemd 服务

### mma-mcp 服务

```bash
sudo tee /etc/systemd/system/mma-mcp.service > /dev/null << 'EOF'
[Unit]
Description=MMA-MCP Server
After=network.target

[Service]
User=mma
Group=mma
WorkingDirectory=/opt/mma-mcp
EnvironmentFile=/etc/default/mma-mcp
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
EnvironmentFile=/etc/default/mma-mcp
ExecStart=/usr/local/bin/caddy run --config /opt/mma-mcp/Caddyfile
Restart=on-failure
RestartSec=5

# Caddy 数据目录（mma 用户没有 home 目录）
Environment=XDG_CONFIG_HOME=/opt/mma-mcp/.caddy/config
Environment=XDG_DATA_HOME=/opt/mma-mcp/.caddy/data

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
curl -s https://mma.<your-domain>/.well-known/oauth-authorization-server | python3 -m json.tool
# 应返回包含 authorization_endpoint、token_endpoint 等字段的 JSON

# 检查 MCP 端点（需要认证，预期返回 401）
curl -v https://mma.<your-domain>/mcp
# 应返回 401 Unauthorized，说明认证中间件正常工作

# 模式 B 用户可直接带 token 测试：
# source /etc/default/mma-mcp
# curl -v -H "Authorization: Bearer $MMA_MCP_AUTH_TOKEN" https://mma.<your-domain>/mcp
# 应返回 405 Method Not Allowed（MCP 端点不接受 GET，说明服务正常）

# 如果证书未签发，检查 Caddy 日志：
sudo journalctl -u caddy-mma --no-pager | tail -30
```

---

## 九、Claude Web 连通测试

1. 打开 https://claude.ai
2. Settings -> Connectors -> Add custom connector
3. URL: `https://mma.<your-domain>/mcp`
4. 会走 OAuth 2.1 认证流程：
   - 模式 A：输入 client ID（如 `claude`）和密码
   - 模式 B：仅输入密码（即 auth token）
5. 测试对话——使用明确的 Wolfram Language 表达式，确保工具被调用：
   - "用 evaluate 执行：`DSolve[y''[x] + x y[x] == 0, y[x], x]`"
   - "用 evaluate 执行：`Series[Exp[x], {x, 0, 5}]`"
   - "用 evaluate_image 执行：`Plot[Sin[x], {x, 0, 2 Pi}]`"

> **ChatGPT 用户：** 在 ChatGPT 中使用 MCP 需要先到 Settings -> Apps -> Advanced settings 中打开 "Developer mode"，然后通过"Create app"添加 MCP 服务器连接。

---

## 十、fail2ban 防护（可选）

用 fail2ban 在 IP 层封禁路径扫描器和登录暴力破解。需要两个 filter：

### 安装

```bash
sudo apt-get install -y fail2ban
```

### Filter 1：路径扫描检测

检测对无效路径的探测（`/admin`、`/.env`、`/wp-login.php` 等），排除所有合法端点。

```bash
sudo tee /etc/fail2ban/filter.d/mma-mcp-probe.conf > /dev/null << 'EOF'
[Definition]
datepattern = {NONE}

# 匹配 uvicorn access log 中非合法路径的 401/403/404 响应
# 排除: /, /mcp, /oauth/*, /.well-known/*, /favicon.ico
failregex = ^.*\b<HOST>(?::\d+)?\s+-\s+"[A-Z]+\s+/(?!(?:$|mcp(?:[/? ]|$)|oauth[/? ]|\.well-known[/? ]|favicon\.ico(?:[? ]|$)))\S+\s+HTTP/\d(?:\.\d+)?"\s+(?:401|403|404)\b

ignoreregex =
EOF
```

### Filter 2：登录暴力破解检测

匹配应用层输出的 `AUTH_FAIL` 日志（包含客户端 IP）。

```bash
sudo tee /etc/fail2ban/filter.d/mma-mcp-auth.conf > /dev/null << 'EOF'
[Definition]
datepattern = {NONE}

failregex = AUTH_FAIL ip=<HOST>\s

ignoreregex =
EOF
```

### Jail 配置

```bash
sudo tee /etc/fail2ban/jail.d/mma-mcp.local > /dev/null << 'EOF'
# 路径扫描：5 次 404 → ban 12 小时
[mma-mcp-probe]
enabled   = true
backend   = systemd
journalmatch = _SYSTEMD_UNIT=mma-mcp.service
filter    = mma-mcp-probe
banaction = nftables[type=allports]
protocol  = tcp
findtime  = 10m
maxretry  = 5
bantime   = 12h

# 登录暴力破解：5 次失败 → ban 1 小时
[mma-mcp-auth]
enabled   = true
backend   = systemd
journalmatch = _SYSTEMD_UNIT=mma-mcp.service
filter    = mma-mcp-auth
banaction = nftables[type=allports]
protocol  = tcp
findtime  = 10m
maxretry  = 5
bantime   = 1h
EOF
```

### 启用

```bash
sudo systemctl enable --now fail2ban
sudo fail2ban-client reload

# 检查 jail 状态
sudo fail2ban-client status mma-mcp-probe
sudo fail2ban-client status mma-mcp-auth

# 手动解封 IP（如有误封）
sudo fail2ban-client set mma-mcp-probe unbanip <IP>
```

> **说明：** 应用层已有指数退避防护（5 次失败后逐步加锁到最长 15 分钟），
> fail2ban 是第二层防线，直接在网络层丢弃恶意 IP 的所有流量。
> 登录 jail 的 `bantime` 设为 1 小时（比扫描的 12 小时短），
> 因为合法用户输错密码的概率更高。

---

## 排障

```bash
# mma-mcp 启动失败
sudo journalctl -u mma-mcp -e

# Caddy 证书签发失败
# - 检查 DNS API 凭据是否正确
# - 检查 A 记录是否已生效: dig mma.<your-domain>
# - 检查环境变量: sudo cat /etc/default/mma-mcp

# 内核找不到
sudo -u mma /opt/mma-mcp/.venv/bin/python -c "from mma_mcp.kernel import find_kernel; print(find_kernel())"

# 手动测试 HTTP 端
sudo -u mma /opt/mma-mcp/.venv/bin/mma-mcp serve --transport http --host 127.0.0.1 --port 8000
# 另一个终端:
curl http://127.0.0.1:8000/mcp
```
