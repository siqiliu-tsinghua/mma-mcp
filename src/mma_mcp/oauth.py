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
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
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
_TOKEN_TTL = 86400  # 24 hours


@dataclass
class _AuthCode:
    client_id: str
    redirect_uri: str
    code_challenge: str
    expires_at: float
    client_name: str = ""
    role: str = ""


@dataclass
class _TokenInfo:
    client_id: str
    expires_at: float
    client_name: str = ""
    role: str = ""


@dataclass
class _ClientInfo:
    client_id: str
    redirect_uris: list[str]


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
    ) -> None:
        self._password = password
        self._auth_config = auth_config
        self._multi_client = auth_config is not None and auth_config.enabled
        # In-memory stores (single process, no persistence needed)
        self._clients: dict[str, _ClientInfo] = {}
        self._auth_codes: dict[str, _AuthCode] = {}
        self._access_tokens: dict[str, _TokenInfo] = {}

    @property
    def multi_client(self) -> bool:
        return self._multi_client

    # ------------------------------------------------------------------
    # Token validation (called by auth middleware)
    # ------------------------------------------------------------------

    def validate_token(self, token: str) -> bool:
        """Return True if *token* is valid (legacy mode only)."""
        if self._password and hmac.compare_digest(token, self._password):
            return True
        info = self._access_tokens.get(token)
        if info is not None:
            if time.time() < info.expires_at:
                return True
            del self._access_tokens[token]
        return False

    def get_token_client(self, token: str) -> "ClientIdentity | None":
        """Return the ClientIdentity for an OAuth-issued token, or None."""
        from mma_mcp.auth import ClientIdentity

        info = self._access_tokens.get(token)
        if info is None:
            return None
        if time.time() >= info.expires_at:
            del self._access_tokens[token]
            return None
        if not info.client_name:
            return None
        return ClientIdentity(client_id=info.client_name, role=info.role)

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

        client_id = secrets.token_urlsafe(24)
        redirect_uris = body.get("redirect_uris", [])
        if not isinstance(redirect_uris, list):
            return JSONResponse(
                {"error": "invalid_client_metadata"}, status_code=400,
            )

        self._clients[client_id] = _ClientInfo(
            client_id=client_id,
            redirect_uris=redirect_uris,
        )
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
        client = self._clients.get(client_id)
        if client is None:
            return HTMLResponse("Unknown client_id", status_code=400)
        if redirect_uri not in client.redirect_uris:
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

        # Issue access token
        access_token = secrets.token_urlsafe(48)
        self._access_tokens[access_token] = _TokenInfo(
            client_id=client_id,
            expires_at=time.time() + _TOKEN_TTL,
            client_name=auth_code.client_name,
            role=auth_code.role,
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
        """
        password = str(form.get("password", ""))

        if self._multi_client:
            client_name = str(form.get("username", ""))  # HTML form field name
            if not client_name:
                return "", "", "Client ID is required"
            assert self._auth_config is not None
            client_conf = self._auth_config.clients.get(client_name)
            if client_conf is None:
                return "", "", "Invalid client ID or password"
            from mma_mcp.passwords import verify_password
            if not verify_password(password, client_conf.password_hash):
                return "", "", "Invalid client ID or password"
            return client_name, client_conf.role, ""
        else:
            # Legacy single-password mode
            if not hmac.compare_digest(password, self._password):
                return "", "", "Password incorrect"
            return "", "", ""

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
