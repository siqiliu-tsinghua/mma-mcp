# Security Policy

## Scope

This policy covers the **mma-mcp** code itself (expression filtering, authentication, OAuth, RBAC). Issues in the Wolfram Engine / Mathematica kernel are outside scope — please report those to [Wolfram Research](https://www.wolfram.com/support/).

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Reporting a Vulnerability

If you find a security vulnerability in mma-mcp, **please do not open a public issue**. Instead:

1. Email **[the repository owner]** with a description of the vulnerability, steps to reproduce, and potential impact.
2. You will receive an acknowledgment within 72 hours.
3. A fix will be developed privately and released as a patch before public disclosure.

If you prefer, you can also use [GitHub private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability) on this repository.

## Security Architecture

mma-mcp uses a three-layer defense model:

### Layer 1: Authentication (`auth.py` / `oauth.py`)

- **Multi-client OAuth 2.1**: DCR + PKCE + Authorization Code grant for web MCP clients
- **Legacy Bearer token**: static token from environment variable
- **Password hashing**: stdlib `hashlib.scrypt` (N=16384, r=8, p=1), timing-safe verification
- **Brute-force protection**: exponential backoff lockout (up to 15 min) + optional fail2ban at the IP level

### Layer 2: Role-Based Access Control (`tools/__init__.py`)

- Per-role tool permissions (which MCP tools a client can call)
- Per-role resource limits (timeout, result size)
- Enforced via `current_client` contextvar, concurrent-safe

### Layer 3: Expression Filtering (`security/`)

- **Pre-kernel filtering**: expressions are analyzed in Python before the Wolfram kernel sees them
- **Symbol extraction**: multi-pass regex tokenizer handles `Symbol["X"]`, context-qualified names (`System`Run`), `<<` (Get) operator, string literals, and comments
- **29 capability groups**: 22 safe (math, visualization, etc.) + 7 dangerous (system_exec, file I/O, networking, dynamic_eval, system_mutation)
- **Two modes**: blacklist (default, blocks dangerous groups) and whitelist (only allows specified groups)

### Known Limitations

- **Dynamic string concatenation**: Expressions like `ToExpression["Ru" <> "n"]` cannot be statically detected. Mitigated by blocking `ToExpression` in the `dynamic_eval` group.
- **Kernel-level state mutation**: While `system_mutation` group blocks `SetOptions`, `Unprotect`, etc., the periodic worker restart (`max_requests_per_worker`) serves as a backstop.

### Proxy Header Trust

`x-forwarded-proto` and `x-forwarded-host` headers are only trusted when the request originates from loopback (`127.0.0.1` / `::1`), preventing spoofing when the server is directly exposed.

## Recommended Deployment Practices

- Bind mma-mcp to `127.0.0.1`, use a reverse proxy (Caddy) for TLS termination
- Enable `[auth]` with per-client credentials for HTTP transport
- Use fail2ban for IP-level brute-force protection (see `DEPLOY.md`)
- Regenerate security groups after Wolfram Engine upgrades: `mma-mcp setup --force`
- Review the default `deny_groups` list and adjust for your use case
