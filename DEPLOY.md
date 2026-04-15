# mma-mcp HTTPS Deployment Guide

> **Use case:** This guide is for individual users who hold a Wolfram Engine / Mathematica license and want to expose their Wolfram kernel over HTTPS to their own web/mobile AI clients (Claude.ai, ChatGPT, etc.), so they can use Wolfram computation from any device. The deployed service is for the license holder's personal use only. Whether other use cases are permitted depends on your Wolfram license terms.

> **Target platform:** This guide targets **Debian / Ubuntu** Linux. Package names and systemd details may differ on other distributions. Contributions for other platforms are welcome (see [CONTRIBUTING.md](CONTRIBUTING.md)).

## Prerequisites

- Debian / Ubuntu server with Wolfram Engine / Mathematica installed and licensed
- Graphics export libraries (required for `evaluate_image` on headless servers):
  ```bash
  sudo apt-get install -y libfontconfig1 libgl1 libasound2t64 libxkbcommon0 libegl1
  ```
- A domain name pointing to your server's public IP
- Port 443 available (and port 80 if using HTTP-01 challenge)

---

## 1. Environment Setup

```bash
# 1. Clone the project and move to /opt
git clone https://github.com/<owner>/mma-mcp.git
sudo mv mma-mcp /opt/mma-mcp
cd /opt/mma-mcp

# 2. Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc

# 3. Install dependencies (venv paths will use /opt/mma-mcp)
uv sync

# 4. Verify kernel path
which WolframKernel
# If not found, or you want to use a different kernel, set:
#   export WOLFRAM_KERNEL=/path/to/WolframKernel

# 5. Generate security groups (~1 min, queries local kernel for symbol classification)
uv run mma-mcp setup
```

---

## 2. DNS Configuration

```bash
# Add an A record in your DNS provider's console:
#   Host: mma (or your preferred subdomain)
#   Value: <server public IP>
#   TTL: 600

# Verify DNS propagation
dig mma.<your-domain> +short
# Should return your server's public IP
```

---

## 3. Configure mma-mcp

```bash
# Generate default config
uv run mma-mcp init
```

Edit `mma_mcp.toml` with the following key settings:

```toml
[kernel]
# Leave empty if `which WolframKernel` works
# Otherwise specify the full path:
# mathkernel = "/path/to/WolframKernel"

[server]
transport = "http"
host = "127.0.0.1"
port = 8000

[tls]
enabled = true
domain = "mma.<your-domain>"
# dns_provider = ""  # see "TLS Certificate" section below
```

### TLS Certificate

Caddy automatically obtains Let's Encrypt certificates. Two ACME challenge modes are available:

**HTTP-01 challenge (simplest)**

No DNS provider needed. Caddy validates domain ownership via port 80. Just leave `dns_provider` empty:

```toml
[tls]
enabled = true
domain = "mma.<your-domain>"
# dns_provider not set -> HTTP-01 challenge (port 80 must be open)
```

No custom Caddy build required — the stock Caddy binary works. Skip to "Generate Caddyfile" below.

**DNS-01 challenge (no port 80 needed)**

Caddy validates domain ownership via DNS API. Useful when port 80 is blocked or you want wildcard certificates. Requires a custom Caddy build with your DNS provider plugin.

Supported DNS providers:

| Provider | `dns_provider` value | Environment variables |
|----------|---------------------|-----------------------|
| Alibaba Cloud DNS | `alidns` | `ALIDNS_ACCESS_KEY_ID`, `ALIDNS_ACCESS_KEY_SECRET` |
| Cloudflare | `cloudflare` | `CLOUDFLARE_API_TOKEN` |
| DNSPod (Tencent Cloud) | `dnspod` | `DNSPOD_API_TOKEN` |
| GoDaddy | `godaddy` | `GODADDY_API_KEY`, `GODADDY_API_SECRET` |
| Namecheap | `namecheap` | `NAMECHEAP_API_KEY`, `NAMECHEAP_API_USER` |

Example with Alibaba Cloud DNS:

```toml
[tls]
enabled = true
domain = "mma.<your-domain>"
dns_provider = "alidns"
```

### Generate Caddyfile

```bash
uv run mma-mcp caddyfile
cat Caddyfile   # Review the output
```

### Authentication Mode

mma-mcp supports two HTTP authentication modes:

**Mode A: Multi-client OAuth (recommended)**

For accessing the same Wolfram kernel from multiple AI clients (e.g., Claude.ai and ChatGPT simultaneously). Each client gets independent credentials and optional permission policies.

Step 1: Uncomment `[auth]` and `[auth.roles.default]` in `mma_mcp.toml` (they are pre-written as comments by `mma-mcp init`):

```toml
[auth]
enabled = true

[auth.roles.default]
tools = "*"
security = ""
```

Step 2: Generate client credentials:

```bash
# Using alice as an example:
uv run mma-mcp add-client alice --role default
# Enter password at prompt, paste the generated TOML into mma_mcp.toml

# Add more clients as needed, e.g. bob:
uv run mma-mcp add-client bob --role default
```

Web clients (Claude.ai, etc.) will go through the OAuth 2.1 flow (DCR + PKCE + authorization code). Users enter client ID and password on the login page.

To remove a client, simply delete the corresponding `[auth.clients.<name>]` section from `mma_mcp.toml`.

**Mode B: Static single token (simple setup)**

For single-client scenarios without role differentiation. Do not enable `[auth]` at the same time.

```toml
[server]
auth_token_env = "MMA_MCP_AUTH_TOKEN"   # Read token from environment variable
```

Web clients will still go through the OAuth flow, but the login page shows only a password field (the token is the password).

---

## 4. Create Service User

Create a dedicated runtime user (like Apache's `www-data`). The `mma` user only runs the service — it needs no shell and no tools. A home directory is required because Wolfram kernel writes runtime data (FrontEnd init, font cache, etc.) to `~/.Wolfram`.

```bash
sudo useradd -r -s /usr/sbin/nologin -d /opt/mma-mcp/mmahome mma
sudo mkdir -p /opt/mma-mcp/mmahome
sudo chown -R mma:mma /opt/mma-mcp /opt/mma-mcp/mmahome
```

> **Note:** Binding to low ports (<1024, e.g., 443) requires the Linux `CAP_NET_BIND_SERVICE` capability, not traditional group permissions. This is granted via `AmbientCapabilities` in the Caddy systemd service below.

---

## 5. Install Caddy

**HTTP-01 (stock Caddy):**

```bash
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy
```

**DNS-01 (custom build with DNS plugin):**

```bash
# Install Go (if not already installed)
sudo apt-get install -y golang

# Install xcaddy to system path (kept for future rebuilds)
export GOPATH=/usr/local/share/go
sudo GOPATH=$GOPATH go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest

# Build Caddy with your DNS provider plugin (example: alidns)
sudo $GOPATH/bin/xcaddy build --with github.com/caddy-dns/alidns
# For other providers:
#   --with github.com/caddy-dns/cloudflare
#   --with github.com/caddy-dns/dnspod
#   --with github.com/caddy-dns/godaddy
#   --with github.com/caddy-dns/namecheap

# Install to system path
sudo mv caddy /usr/local/bin/
caddy version
```

---

## 6. Prepare Credentials

The environment file contains secrets that systemd services read at startup.

> **Note:** Do not add spaces around `=` — keys and values must be immediately adjacent to the equals sign (e.g., `KEY=value`, not `KEY = value`). This is a systemd `EnvironmentFile` requirement.

```bash
sudo touch /etc/default/mma-mcp
sudo chmod 600 /etc/default/mma-mcp
sudo editor /etc/default/mma-mcp
```

**If using DNS-01**, add your DNS provider's API credentials (replace `<...>` with your actual keys):

```
# Example: Alibaba Cloud DNS
ALIDNS_ACCESS_KEY_ID=<your-access-key-id>
ALIDNS_ACCESS_KEY_SECRET=<your-access-key-secret>
```

For other providers, use their respective environment variables (see the table in section 3).

**If using HTTP-01**, the file can be left empty (or contain only auth credentials below).

**If using static single token auth** (Mode B), add a token:

```bash
# Generate a random token
openssl rand -hex 32
```

Copy the output and add it to `/etc/default/mma-mcp`:

```
MMA_MCP_AUTH_TOKEN=<paste-token-here>
```

---

## 7. systemd Services

### mma-mcp Service

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

### Caddy Service

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

# Allow binding to port 443 without root
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
EOF
```

### Start Services

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mma-mcp caddy-mma

# Check status
sudo systemctl status mma-mcp
sudo systemctl status caddy-mma

# View logs
sudo journalctl -u mma-mcp -f
sudo journalctl -u caddy-mma -f
```

---

## 8. Verify Deployment

```bash
# Check OAuth metadata endpoint (no auth required)
curl -s https://mma.<your-domain>/.well-known/oauth-authorization-server | python3 -m json.tool
# Should return JSON with authorization_endpoint, token_endpoint, etc.

# Check MCP endpoint (auth required, expect 401)
curl -v https://mma.<your-domain>/mcp
# Should return 401 Unauthorized

# Mode B users can test directly with token:
# source /etc/default/mma-mcp
# curl -v -H "Authorization: Bearer $MMA_MCP_AUTH_TOKEN" https://mma.<your-domain>/mcp
# Should return 405 Method Not Allowed (MCP doesn't accept GET — service is working)

# If certificate isn't issued, check Caddy logs:
sudo journalctl -u caddy-mma --no-pager | tail -30
```

---

## 9. Connect from Claude Web

1. Go to https://claude.ai
2. Settings -> Connectors -> Add custom connector
3. URL: `https://mma.<your-domain>/mcp`
4. Complete the OAuth 2.1 authentication flow:
   - Mode A: enter client ID (e.g., `alice`) and password
   - Mode B: enter password only (the auth token)
5. Test with a conversation — use explicit Wolfram Language expressions to ensure the tool is invoked:
   - "Use evaluate to run: `DSolve[y''[x] + x y[x] == 0, y[x], x]`"
   - "Use evaluate to run: `Series[Exp[x], {x, 0, 5}]`"
   - "Use evaluate_image to run: `Plot[Sin[x], {x, 0, 2 Pi}]`"

> **ChatGPT users:** To use MCP in ChatGPT, first enable "Developer mode" in Settings -> Apps -> Advanced settings, then add the MCP server connection via "Create app".

> **Note on `evaluate_image`:** This tool is experimental. Whether images are displayed depends on the client's internal handling of MCP `ImageContent`. In testing, Claude.ai shows the image folded inside the tool result (click to expand), while ChatGPT does not display it at all. If you don't need image output, you can disable it in `mma_mcp.toml` — see the `[tools]` and `[auth.roles]` sections.

---

## 10. fail2ban Protection (optional)

Use fail2ban to block path scanners and login brute-force attempts at the IP level. Two filters are needed:

### Install

```bash
sudo apt-get install -y fail2ban
```

### Filter 1: Path Scan Detection

Detects probes to invalid paths (`/admin`, `/.env`, `/wp-login.php`, etc.), excluding all legitimate endpoints.

```bash
sudo tee /etc/fail2ban/filter.d/mma-mcp-probe.conf > /dev/null << 'EOF'
[Definition]
datepattern = {NONE}

# Match uvicorn access log entries for non-legitimate paths returning 401/403/404
# Excludes: /, /mcp, /oauth/*, /.well-known/*, /favicon.ico
failregex = ^.*\b<HOST>(?::\d+)?\s+-\s+"[A-Z]+\s+/(?!(?:$|mcp(?:[/? ]|$)|oauth[/? ]|\.well-known[/? ]|favicon\.ico(?:[? ]|$)))\S+\s+HTTP/\d(?:\.\d+)?"\s+(?:401|403|404)\b

ignoreregex =
EOF
```

### Filter 2: Login Brute-Force Detection

Matches application-level `AUTH_FAIL` log entries containing the client IP.

```bash
sudo tee /etc/fail2ban/filter.d/mma-mcp-auth.conf > /dev/null << 'EOF'
[Definition]
datepattern = {NONE}

failregex = AUTH_FAIL ip=<HOST>\s

ignoreregex =
EOF
```

### Jail Configuration

```bash
sudo tee /etc/fail2ban/jail.d/mma-mcp.local > /dev/null << 'EOF'
# Path scanning: 5 hits -> ban 12 hours
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

# Login brute-force: 5 failures -> ban 1 hour
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

### Enable

```bash
sudo systemctl enable --now fail2ban
sudo fail2ban-client reload

# Check jail status
sudo fail2ban-client status mma-mcp-probe
sudo fail2ban-client status mma-mcp-auth

# Manually unban an IP (if needed)
sudo fail2ban-client set mma-mcp-probe unbanip <IP>
```

> **Note:** The application layer already has exponential backoff protection (locking up to 15 minutes after 5 failures). fail2ban is a second layer of defense, dropping all traffic from malicious IPs at the network level. The login jail `bantime` is set to 1 hour (shorter than the 12-hour scanning ban) because legitimate users are more likely to mistype a password.

---

## Troubleshooting

```bash
# mma-mcp fails to start
sudo journalctl -u mma-mcp -e

# Caddy certificate issuance fails
# - Check DNS API credentials
# - Verify A record: dig mma.<your-domain>
# - Check environment variables: sudo cat /etc/default/mma-mcp

# Kernel not found
sudo -u mma /opt/mma-mcp/.venv/bin/python -c "from mma_mcp.kernel import find_kernel; print(find_kernel())"

# Manual HTTP test
sudo -u mma /opt/mma-mcp/.venv/bin/mma-mcp serve --transport http --host 127.0.0.1 --port 8000
# In another terminal:
curl http://127.0.0.1:8000/mcp
```
