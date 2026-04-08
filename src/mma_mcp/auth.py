"""Bearer token authentication middleware for Streamable HTTP transport.

Supports three modes (auto-selected based on configuration):
  1. Multi-client + roles: ``[auth]`` enabled — AI clients authenticate via
     OAuth or ``base64(client_id:password)`` Bearer tokens.  Identity is
     propagated via ``current_client`` contextvar.
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
import time
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
# Client identity (propagated via contextvars)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClientIdentity:
    client_id: str
    role: str


ANONYMOUS = ClientIdentity(client_id="", role="")

current_client: contextvars.ContextVar[ClientIdentity] = contextvars.ContextVar(
    "current_client", default=ANONYMOUS,
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

    In multi-client mode, resolves the token to a ``ClientIdentity`` and sets
    it on the ``current_client`` contextvar so downstream tool wrappers can
    read it.
    """

    # Brute-force protection constants
    _MAX_FAILURES = 5
    _MAX_LOCKOUT_SECS = 900

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
        # Brute-force protection: {client_id: (failure_count, lockout_until)}
        self._login_failures: dict[str, tuple[int, float]] = {}

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

        tok = current_client.set(identity)
        try:
            return await call_next(request)
        finally:
            current_client.reset(tok)

    def _resolve(self, token: str) -> ClientIdentity | None:
        """Resolve a Bearer token to a ClientIdentity, or None if invalid."""

        # --- Multi-client mode ---
        if self._auth_config is not None and self._auth_config.enabled:
            # 1) Check OAuth-issued tokens
            if self._oauth is not None:
                identity = self._oauth.get_token_client(token)
                if identity is not None:
                    return identity

            # 2) CLI path: base64(client_id:password)
            return self._try_basic_token(token)

        # --- Legacy single-token mode ---
        if self._oauth is not None:
            if self._oauth.validate_token(token):
                return ANONYMOUS
        elif self._token and hmac.compare_digest(token, self._token):
            return ANONYMOUS

        return None

    def _try_basic_token(self, token: str) -> ClientIdentity | None:
        """Decode ``base64(client_id:password)`` and verify against auth config.

        Includes brute-force protection with exponential backoff lockout.
        """
        if self._auth_config is None:
            return None
        try:
            decoded = base64.b64decode(token, validate=True).decode("utf-8")
        except Exception:
            return None
        if ":" not in decoded:
            return None
        client_id, password = decoded.split(":", 1)

        # Check lockout
        if self._is_locked_out(client_id):
            return None

        client_conf = self._auth_config.clients.get(client_id)
        if client_conf is None:
            self._record_failure(client_id)
            return None
        from mma_mcp.passwords import verify_password
        if not verify_password(password, client_conf.password_hash):
            self._record_failure(client_id)
            return None
        self._login_failures.pop(client_id, None)
        return ClientIdentity(client_id=client_id, role=client_conf.role)

    def _is_locked_out(self, key: str) -> bool:
        entry = self._login_failures.get(key)
        if entry is None:
            return False
        count, lockout_until = entry
        if count < self._MAX_FAILURES:
            return False
        if time.time() < lockout_until:
            logger.warning("Bearer auth locked out for %s (%d failures)", key, count)
            return True
        return False

    def _record_failure(self, key: str) -> None:
        entry = self._login_failures.get(key)
        count = (entry[0] if entry else 0) + 1
        if count >= self._MAX_FAILURES:
            excess = count - self._MAX_FAILURES
            lockout_secs = min(2 ** excess, self._MAX_LOCKOUT_SECS)
            lockout_until = time.time() + lockout_secs
            logger.warning("Bearer auth failure #%d for %s — lockout %ds", count, key, lockout_secs)
        else:
            lockout_until = 0.0
        self._login_failures[key] = (count, lockout_until)
