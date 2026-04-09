"""Minimal OAuth 2.1 authorization server for MCP web clients.

Implements just enough of the spec for ChatGPT and Claude web to connect:
  - RFC 8414  Authorization Server Metadata
  - RFC 7591  Dynamic Client Registration (DCR)
  - RFC 7636  PKCE (S256 only)
  - Authorization Code grant

Supports two modes:
  - **Multi-client** (``auth_config`` provided): login page shows client_id +
    password, tokens carry client identity and role.
  - **Legacy single-password** (``password`` provided): login page shows
    password only.

Tokens and DCR clients are persisted to SQLite so they survive process
restarts.  Auth codes and login-failure counters remain in memory (short-lived).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

if TYPE_CHECKING:
    from mma_mcp.auth import ClientIdentity
    from mma_mcp.config import AuthConfig

logger = logging.getLogger(__name__)

# Token / code lifetimes
_CODE_TTL = 600  # 10 minutes
_TOKEN_TTL = 2592000  # 30 days

# Brute-force protection
_MAX_LOGIN_FAILURES = 5       # failures before lockout kicks in
_MAX_LOCKOUT_SECS = 900       # 15 minute cap on lockout duration

# Capacity limits
_MAX_DCR_CLIENTS = 100      # max dynamically registered OAuth clients
_MAX_ACCESS_TOKENS = 1000   # max concurrent access tokens
_MAX_AUTH_CODES = 200        # max pending authorization codes


@dataclass
class _AuthCode:
    client_id: str
    redirect_uri: str
    code_challenge: str
    expires_at: float
    client_name: str = ""
    role: str = ""


# ---------------------------------------------------------------------------
# SQLite-backed token / client store
# ---------------------------------------------------------------------------

class _TokenStore:
    """Thin SQLite wrapper for OAuth tokens and DCR clients.

    All data survives process restarts.  WAL mode enables concurrent readers.
    """

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(
            db_path, check_same_thread=False, isolation_level="DEFERRED",
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        # Clean up expired entries left over from a previous run
        self.evict_expired_tokens()
        logger.info("OAuth store opened: %s", db_path)

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS oauth_tokens (
                token       TEXT PRIMARY KEY,
                client_id   TEXT NOT NULL,
                client_name TEXT NOT NULL DEFAULT '',
                role        TEXT NOT NULL DEFAULT '',
                expires_at  REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_clients (
                client_id     TEXT PRIMARY KEY,
                redirect_uris TEXT NOT NULL DEFAULT '[]'
            );
        """)

    # -- tokens --

    def put_token(
        self, token: str, client_id: str, client_name: str,
        role: str, expires_at: float,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO oauth_tokens "
            "(token, client_id, client_name, role, expires_at) VALUES (?,?,?,?,?)",
            (token, client_id, client_name, role, expires_at),
        )
        self._conn.commit()

    def get_token(self, token: str) -> tuple[str, str, str, float] | None:
        """Return (client_id, client_name, role, expires_at) or None."""
        row = self._conn.execute(
            "SELECT client_id, client_name, role, expires_at "
            "FROM oauth_tokens WHERE token = ?", (token,),
        ).fetchone()
        return row

    def delete_token(self, token: str) -> None:
        self._conn.execute("DELETE FROM oauth_tokens WHERE token = ?", (token,))
        self._conn.commit()

    def count_tokens(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM oauth_tokens").fetchone()[0]

    def evict_expired_tokens(self) -> int:
        cur = self._conn.execute(
            "DELETE FROM oauth_tokens WHERE expires_at <= ?", (time.time(),),
        )
        self._conn.commit()
        return cur.rowcount

    # -- clients --

    def put_client(self, client_id: str, redirect_uris: list[str]) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO oauth_clients (client_id, redirect_uris) VALUES (?,?)",
            (client_id, json.dumps(redirect_uris)),
        )
        self._conn.commit()

    def get_client(self, client_id: str) -> tuple[str, list[str]] | None:
        """Return (client_id, redirect_uris) or None."""
        row = self._conn.execute(
            "SELECT client_id, redirect_uris FROM oauth_clients WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        if row is None:
            return None
        return row[0], json.loads(row[1])

    def count_clients(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM oauth_clients").fetchone()[0]

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# OAuth server
# ---------------------------------------------------------------------------

class OAuthServer:
    """Minimal OAuth 2.1 authorization server.

    In multi-client mode (``auth_config`` provided), the login page shows
    client_id + password and tokens carry client identity.  In legacy mode
    (``password`` provided), it behaves as before: single password, no identity.
    """

    def __init__(
        self,
        password: str = "",
        auth_config: "AuthConfig | None" = None,
        db_path: str = "mma_mcp_oauth.db",
    ) -> None:
        self._password = password
        self._auth_config = auth_config
        self._multi_client = auth_config is not None and auth_config.enabled
        # Persistent store (SQLite)
        self._store = _TokenStore(db_path)
        # In-memory only (short-lived, not worth persisting)
        self._auth_codes: dict[str, _AuthCode] = {}
        self._login_failures: dict[str, tuple[int, float]] = {}

    @property
    def multi_client(self) -> bool:
        return self._multi_client

    # ------------------------------------------------------------------
    # Housekeeping — evict expired entries
    # ------------------------------------------------------------------

    def _evict_expired(self) -> None:
        """Remove expired tokens and auth codes."""
        n_tokens = self._store.evict_expired_tokens()
        now = time.time()
        expired_codes = [k for k, v in self._auth_codes.items()
                         if now >= v.expires_at]
        for k in expired_codes:
            del self._auth_codes[k]
        if n_tokens or expired_codes:
            logger.debug(
                "Evicted %d expired tokens, %d expired codes",
                n_tokens, len(expired_codes),
            )

    # ------------------------------------------------------------------
    # Token validation (called by auth middleware)
    # ------------------------------------------------------------------

    def validate_token(self, token: str) -> bool:
        """Return True if *token* is valid (legacy mode only)."""
        if self._password and hmac.compare_digest(token, self._password):
            return True
        row = self._store.get_token(token)
        if row is not None:
            _, _, _, expires_at = row
            if time.time() < expires_at:
                return True
            self._store.delete_token(token)
        return False

    def get_token_client(self, token: str) -> "ClientIdentity | None":
        """Return the ClientIdentity for an OAuth-issued token, or None."""
        from mma_mcp.auth import ClientIdentity

        row = self._store.get_token(token)
        if row is None:
            return None
        client_id, client_name, role, expires_at = row
        if time.time() >= expires_at:
            self._store.delete_token(token)
            return None
        if not client_name:
            return None
        return ClientIdentity(client_id=client_name, role=role)

    # ------------------------------------------------------------------
    # Starlette routes
    # ------------------------------------------------------------------

    def routes(self) -> list[Route]:
        return [
            Route(
                "/.well-known/oauth-authorization-server",
                self._metadata,
                methods=["GET"],
            ),
            Route("/oauth/register", self._register, methods=["POST"]),
            Route("/oauth/authorize", self._authorize, methods=["GET", "POST"]),
            Route("/oauth/token", self._token, methods=["POST"]),
        ]

    # --- Metadata discovery (RFC 8414) ---

    async def _metadata(self, request: Request) -> JSONResponse:
        base = self._base_url(request)
        return JSONResponse({
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        })

    # --- Dynamic Client Registration (RFC 7591) ---

    async def _register(self, request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        # Capacity check
        if self._store.count_clients() >= _MAX_DCR_CLIENTS:
            logger.warning("DCR: capacity limit reached (%d clients)", _MAX_DCR_CLIENTS)
            return JSONResponse(
                {"error": "server_error",
                 "error_description": "Too many registered clients"},
                status_code=503,
            )

        client_id = secrets.token_urlsafe(24)
        redirect_uris = body.get("redirect_uris", [])
        if not isinstance(redirect_uris, list):
            return JSONResponse(
                {"error": "invalid_client_metadata"}, status_code=400,
            )

        self._store.put_client(client_id, redirect_uris)
        logger.info("DCR: registered client %s", client_id)

        return JSONResponse({
            "client_id": client_id,
            "redirect_uris": redirect_uris,
            "token_endpoint_auth_method": "none",
        }, status_code=201)

    # --- Authorization endpoint ---

    async def _authorize(self, request: Request) -> HTMLResponse | RedirectResponse | JSONResponse:
        if request.method == "GET":
            return self._authorize_form(request)
        else:
            return await self._authorize_submit(request)

    def _authorize_form(self, request: Request) -> HTMLResponse:
        """Render the login page."""
        params = request.query_params
        hidden = self._build_hidden_fields(params)
        return HTMLResponse(self._render_login(hidden_fields=hidden, error=""))

    async def _authorize_submit(self, request: Request) -> RedirectResponse | HTMLResponse:
        """Validate credentials, issue auth code, redirect back."""
        form = await request.form()
        redirect_uri = str(form.get("redirect_uri", ""))
        client_id = str(form.get("client_id", ""))
        code_challenge = str(form.get("code_challenge", ""))
        code_challenge_method = str(form.get("code_challenge_method", ""))
        state = str(form.get("state", ""))

        # Authenticate
        client_name, role, err = self._check_credentials(form)
        if err:
            hidden = self._build_hidden_fields(form)
            return HTMLResponse(
                self._render_login(hidden_fields=hidden, error=err),
                status_code=200,
            )

        # Validate required OAuth params
        if not redirect_uri or not client_id:
            return HTMLResponse("Missing redirect_uri or client_id", status_code=400)

        # Validate client_id is registered and redirect_uri matches
        client = self._store.get_client(client_id)
        if client is None:
            return HTMLResponse("Unknown client_id", status_code=400)
        _, registered_uris = client
        if redirect_uri not in registered_uris:
            logger.warning(
                "OAuth: redirect_uri %r not in registered URIs for client %s",
                redirect_uri, client_id,
            )
            return HTMLResponse("redirect_uri not registered for this client", status_code=400)

        # PKCE is mandatory (OAuth 2.1)
        if not code_challenge:
            return HTMLResponse("PKCE code_challenge is required", status_code=400)
        if code_challenge_method and code_challenge_method != "S256":
            return HTMLResponse("Only S256 is supported", status_code=400)

        # Evict expired entries before issuing new code
        self._evict_expired()
        if len(self._auth_codes) >= _MAX_AUTH_CODES:
            return HTMLResponse("Too many pending authorizations, try again later", status_code=503)

        # Issue authorization code
        code = secrets.token_urlsafe(32)
        self._auth_codes[code] = _AuthCode(
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            expires_at=time.time() + _CODE_TTL,
            client_name=client_name,
            role=role,
        )
        logger.info("Issued auth code for client=%s role=%s oauth_client=%s", client_name, role, client_id)

        # Redirect back
        sep = "&" if "?" in redirect_uri else "?"
        location = f"{redirect_uri}{sep}code={code}"
        if state:
            location += f"&state={state}"
        return RedirectResponse(location, status_code=302)

    # --- Token endpoint ---

    async def _token(self, request: Request) -> JSONResponse:
        # Accept both form-encoded and JSON
        content_type = request.headers.get("content-type", "")
        if "json" in content_type:
            try:
                params = await request.json()
            except Exception:
                return JSONResponse({"error": "invalid_request"}, status_code=400)
        else:
            form = await request.form()
            params = dict(form)

        grant_type = params.get("grant_type", "")
        if grant_type != "authorization_code":
            return JSONResponse(
                {"error": "unsupported_grant_type"}, status_code=400,
            )

        code = params.get("code", "")
        code_verifier = params.get("code_verifier", "")
        client_id = params.get("client_id", "")
        redirect_uri = params.get("redirect_uri", "")

        # Look up and consume auth code (one-time use)
        auth_code = self._auth_codes.pop(code, None)
        if auth_code is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)

        # Check expiry
        if time.time() > auth_code.expires_at:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)

        # Validate client_id and redirect_uri
        if auth_code.client_id != client_id:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if auth_code.redirect_uri != redirect_uri:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)

        # Verify PKCE
        if auth_code.code_challenge:
            if not code_verifier:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            if not _verify_pkce(code_verifier, auth_code.code_challenge):
                return JSONResponse({"error": "invalid_grant"}, status_code=400)

        # Evict expired tokens before issuing new one
        self._evict_expired()
        if self._store.count_tokens() >= _MAX_ACCESS_TOKENS:
            return JSONResponse(
                {"error": "server_error",
                 "error_description": "Too many active tokens"},
                status_code=503,
            )

        # Issue access token
        access_token = secrets.token_urlsafe(48)
        self._store.put_token(
            token=access_token,
            client_id=client_id,
            client_name=auth_code.client_name,
            role=auth_code.role,
            expires_at=time.time() + _TOKEN_TTL,
        )
        logger.info(
            "Issued access token for client=%s role=%s oauth_client=%s",
            auth_code.client_name, auth_code.role, client_id,
        )

        return JSONResponse({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": _TOKEN_TTL,
        })

    # ------------------------------------------------------------------
    # Credential checking
    # ------------------------------------------------------------------

    def _check_credentials(self, form) -> tuple[str, str, str]:  # noqa: ANN001
        """Validate the submitted form credentials.

        Returns ``(client_name, role, error_message)``.  *error_message* is
        empty on success.

        Includes brute-force protection: after ``_MAX_LOGIN_FAILURES``
        consecutive failures for a given username, subsequent attempts are
        locked out with exponential backoff (capped at ``_MAX_LOCKOUT_SECS``).
        """
        password = str(form.get("password", ""))

        if self._multi_client:
            client_name = str(form.get("username", ""))  # HTML form field name
            if not client_name:
                return "", "", "Client ID is required"

            # Check lockout
            lockout_err = self._check_lockout(client_name)
            if lockout_err:
                return "", "", lockout_err

            assert self._auth_config is not None
            client_conf = self._auth_config.clients.get(client_name)
            if client_conf is None:
                self._record_failure(client_name)
                return "", "", "Invalid client ID or password"
            from mma_mcp.passwords import verify_password
            if not verify_password(password, client_conf.password_hash):
                self._record_failure(client_name)
                return "", "", "Invalid client ID or password"
            self._clear_failures(client_name)
            return client_name, client_conf.role, ""
        else:
            # Legacy single-password mode — use fixed key for rate limiting
            key = "__legacy__"
            lockout_err = self._check_lockout(key)
            if lockout_err:
                return "", "", lockout_err
            if not hmac.compare_digest(password, self._password):
                self._record_failure(key)
                return "", "", "Password incorrect"
            self._clear_failures(key)
            return "", "", ""

    # ------------------------------------------------------------------
    # Brute-force rate limiting
    # ------------------------------------------------------------------

    def _check_lockout(self, key: str) -> str:
        """Return an error message if *key* is currently locked out, else ''."""
        entry = self._login_failures.get(key)
        if entry is None:
            return ""
        count, lockout_until = entry
        if count < _MAX_LOGIN_FAILURES:
            return ""
        now = time.time()
        if now < lockout_until:
            remaining = int(lockout_until - now) + 1
            logger.warning("Login locked out for %s (%d failures, %ds remaining)", key, count, remaining)
            return f"Too many failed attempts. Try again in {remaining}s."
        return ""

    def _record_failure(self, key: str) -> None:
        """Record a failed login attempt and compute lockout duration."""
        entry = self._login_failures.get(key)
        count = (entry[0] if entry else 0) + 1
        if count >= _MAX_LOGIN_FAILURES:
            # Exponential backoff: 2^(excess) seconds, capped
            excess = count - _MAX_LOGIN_FAILURES
            lockout_secs = min(2 ** excess, _MAX_LOCKOUT_SECS)
            lockout_until = time.time() + lockout_secs
            logger.warning("Login failure #%d for %s — locked out for %ds", count, key, lockout_secs)
        else:
            lockout_until = 0.0
        self._login_failures[key] = (count, lockout_until)

    def _clear_failures(self, key: str) -> None:
        """Clear failure count on successful login."""
        self._login_failures.pop(key, None)

    # ------------------------------------------------------------------
    # HTML rendering
    # ------------------------------------------------------------------

    def _render_login(self, hidden_fields: str, error: str) -> str:
        """Render the login HTML page."""
        error_html = (
            f'<p style="color:#e74c3c;margin-bottom:16px">{_esc(error)}</p>'
            if error else ""
        )
        if self._multi_client:
            username_field = (
                '<label for="username">Client ID</label>\n'
                '<input type="text" id="username" name="username" '
                'autocomplete="username" autofocus required '
                'style="width:100%;padding:10px 12px;border:1px solid #ddd;'
                'border-radius:8px;font-size:15px;box-sizing:border-box;'
                'margin-bottom:12px">\n'
            )
            pwd_autofocus = ""
        else:
            username_field = ""
            pwd_autofocus = " autofocus"

        return _LOGIN_HTML.format(
            hidden_fields=hidden_fields,
            error_html=error_html,
            username_field=username_field,
            pwd_autofocus=pwd_autofocus,
        )

    @staticmethod
    def _build_hidden_fields(params) -> str:  # noqa: ANN001
        """Build hidden input fields from query params or form data."""
        hidden = ""
        for key in ("response_type", "client_id", "redirect_uri",
                     "code_challenge", "code_challenge_method", "state", "scope"):
            val = str(params.get(key, ""))
            if val:
                hidden += f'<input type="hidden" name="{key}" value="{_esc(val)}">\n'
        return hidden

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _base_url(request: Request) -> str:
        """Derive the public base URL from the request."""
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
        return f"{scheme}://{host}"


def _verify_pkce(verifier: str, challenge: str) -> bool:
    """Verify PKCE S256: BASE64URL(SHA256(verifier)) == challenge."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(computed, challenge)


def _esc(s: str) -> str:
    """Minimal HTML attribute escaping."""
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


# ------------------------------------------------------------------
# Login page HTML
# ------------------------------------------------------------------

_LOGIN_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>mma-mcp — Authorize</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh; margin: 0;
    background: #f5f5f5; color: #333;
  }}
  .card {{
    background: #fff; border-radius: 12px; padding: 40px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.08);
    width: 100%; max-width: 380px;
  }}
  h1 {{ font-size: 20px; margin: 0 0 8px; }}
  p.sub {{ color: #888; font-size: 14px; margin: 0 0 24px; }}
  label {{ font-size: 14px; font-weight: 500; display: block; margin-bottom: 6px; }}
  input[type=password], input[type=text] {{
    width: 100%; padding: 10px 12px; border: 1px solid #ddd;
    border-radius: 8px; font-size: 15px; box-sizing: border-box;
  }}
  input:focus {{ outline: none; border-color: #4a90d9; }}
  button {{
    width: 100%; padding: 10px; margin-top: 16px;
    background: #4a90d9; color: #fff; border: none; border-radius: 8px;
    font-size: 15px; cursor: pointer;
  }}
  button:hover {{ background: #357abd; }}
</style>
</head>
<body>
<div class="card">
  <h1>mma-mcp</h1>
  <p class="sub">Wolfram Engine MCP Server</p>
  {error_html}
  <form method="POST">
    {hidden_fields}
    {username_field}
    <label for="password">Password</label>
    <input type="password" id="password" name="password"{pwd_autofocus} required>
    <button type="submit">Authorize</button>
  </form>
</div>
</body>
</html>
"""
