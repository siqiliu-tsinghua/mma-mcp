"""Bearer token authentication middleware for Streamable HTTP transport.

Supports three modes (auto-selected based on configuration):
  1. Multi-user + roles: ``[auth]`` enabled — users authenticate via OAuth or
     ``base64(username:password)`` Bearer tokens. Identity is propagated via
     ``current_user`` contextvar.
  2. Legacy single-token: ``server.auth_token_env`` set — static Bearer token.
  3. No auth: neither configured — middleware not mounted.

OAuth endpoints are excluded from auth checks so that the authorization flow
can proceed.
"""

from __future__ import annotations

import base64
import contextvars
import hmac
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from mma_mcp.config import AuthConfig
    from mma_mcp.oauth import OAuthServer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# User identity (propagated via contextvars)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UserIdentity:
    username: str
    role: str


ANONYMOUS = UserIdentity(username="", role="")

current_user: contextvars.ContextVar[UserIdentity] = contextvars.ContextVar(
    "current_user", default=ANONYMOUS,
)

# ---------------------------------------------------------------------------
# Paths that must be accessible without a Bearer token
# ---------------------------------------------------------------------------

_PUBLIC_PREFIXES = (
    "/.well-known/",
    "/oauth/",
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Reject requests without a valid Bearer token.

    In multi-user mode, resolves the token to a ``UserIdentity`` and sets it
    on the ``current_user`` contextvar so downstream tool wrappers can read it.
    """

    def __init__(
        self,
        app,  # noqa: ANN001
        token: str = "",
        oauth_server: "OAuthServer | None" = None,
        auth_config: "AuthConfig | None" = None,
    ) -> None:
        super().__init__(app)
        self._token = token
        self._oauth = oauth_server
        self._auth_config = auth_config

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        # OAuth / metadata endpoints are always public
        if any(request.url.path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse({"error": "Missing Bearer token"}, status_code=401)

        provided = auth_header.removeprefix("Bearer ")

        identity = self._resolve(provided)
        if identity is None:
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        tok = current_user.set(identity)
        try:
            return await call_next(request)
        finally:
            current_user.reset(tok)

    def _resolve(self, token: str) -> UserIdentity | None:
        """Resolve a Bearer token to a UserIdentity, or None if invalid."""

        # --- Multi-user mode ---
        if self._auth_config is not None and self._auth_config.enabled:
            # 1) Check OAuth-issued tokens
            if self._oauth is not None:
                identity = self._oauth.get_token_user(token)
                if identity is not None:
                    return identity

            # 2) CLI path: base64(username:password)
            return self._try_basic_token(token)

        # --- Legacy single-token mode ---
        if self._oauth is not None:
            if self._oauth.validate_token(token):
                return ANONYMOUS
        elif self._token and hmac.compare_digest(token, self._token):
            return ANONYMOUS

        return None

    def _try_basic_token(self, token: str) -> UserIdentity | None:
        """Decode ``base64(username:password)`` and verify against auth config."""
        if self._auth_config is None:
            return None
        try:
            decoded = base64.b64decode(token, validate=True).decode("utf-8")
        except Exception:
            return None
        if ":" not in decoded:
            return None
        username, password = decoded.split(":", 1)
        user_conf = self._auth_config.users.get(username)
        if user_conf is None:
            return None
        from mma_mcp.passwords import verify_password
        if not verify_password(password, user_conf.password_hash):
            return None
        return UserIdentity(username=username, role=user_conf.role)
