"""Unit tests for auth.py, oauth.py, and passwords.py."""

from __future__ import annotations

import base64

import pytest

from mma_mcp.auth import ANONYMOUS, BearerAuthMiddleware, ClientIdentity, current_client
from mma_mcp.config import AuthConfig, ClientConfig, RoleConfig
from mma_mcp.oauth import OAuthServer, _verify_pkce
from mma_mcp.passwords import hash_password, verify_password


# ===================================================================
# passwords.py
# ===================================================================

class TestPasswords:

    def test_hash_and_verify(self):
        h = hash_password("secret123")
        assert verify_password("secret123", h)

    def test_wrong_password_fails(self):
        h = hash_password("secret123")
        assert not verify_password("wrong", h)

    def test_hash_format(self):
        h = hash_password("test")
        parts = h.split(":")
        assert len(parts) == 3
        assert parts[0] == "scrypt"
        # salt is 16 bytes → 32 hex chars
        assert len(parts[1]) == 32
        # hash is 32 bytes → 64 hex chars
        assert len(parts[2]) == 64

    def test_different_salts(self):
        """Two hashes of the same password should differ (random salt)."""
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2
        assert verify_password("same", h1)
        assert verify_password("same", h2)

    def test_invalid_hash_format(self):
        assert not verify_password("test", "invalid")
        assert not verify_password("test", "scrypt:bad")
        assert not verify_password("test", "bcrypt:aa:bb")
        assert not verify_password("test", "scrypt:not_hex:not_hex")


# ===================================================================
# PKCE verification
# ===================================================================

class TestPKCE:

    def test_valid_pkce_s256(self):
        import hashlib
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        assert _verify_pkce(verifier, challenge)

    def test_invalid_pkce(self):
        assert not _verify_pkce("wrong_verifier", "wrong_challenge")


# ===================================================================
# OAuthServer — legacy single-password mode
# ===================================================================

class TestOAuthServerLegacy:

    def test_validate_token_with_password(self):
        srv = OAuthServer(password="my-secret", db_path=":memory:")
        assert srv.validate_token("my-secret")
        assert not srv.validate_token("wrong")

    def test_get_token_client_returns_none_legacy(self):
        srv = OAuthServer(password="my-secret", db_path=":memory:")
        assert srv.get_token_client("anything") is None

    def test_multi_client_is_false(self):
        srv = OAuthServer(password="my-secret", db_path=":memory:")
        assert not srv.multi_client


# ===================================================================
# OAuthServer — multi-client mode
# ===================================================================

class TestOAuthServerMultiClient:

    @pytest.fixture
    def auth_config(self):
        pwd_hash = hash_password("alice-pass")
        return AuthConfig(
            enabled=True,
            roles={"admin": RoleConfig(tools="*", security="none")},
            clients={"alice": ClientConfig(role="admin", password_hash=pwd_hash)},
        )

    @pytest.fixture
    def srv(self, auth_config):
        return OAuthServer(auth_config=auth_config, db_path=":memory:")

    def test_multi_client_is_true(self, srv):
        assert srv.multi_client

    def test_routes_exist(self, srv):
        routes = srv.routes()
        paths = [r.path for r in routes]
        assert "/.well-known/oauth-authorization-server" in paths
        assert "/oauth/register" in paths
        assert "/oauth/authorize" in paths
        assert "/oauth/token" in paths


# ===================================================================
# BearerAuthMiddleware — _resolve (via _try_basic_token)
# ===================================================================

class TestBearerAuthResolve:

    @pytest.fixture
    def auth_config(self):
        pwd_hash = hash_password("bob-pass")
        return AuthConfig(
            enabled=True,
            roles={"reader": RoleConfig()},
            clients={"bob": ClientConfig(role="reader", password_hash=pwd_hash)},
        )

    def test_basic_token_valid(self, auth_config):
        """base64(client_id:password) resolves to correct identity."""
        middleware = BearerAuthMiddleware(
            app=None, auth_config=auth_config,
        )
        token = base64.b64encode(b"bob:bob-pass").decode()
        identity = middleware._resolve(token)
        assert identity is not None
        assert identity.client_id == "bob"
        assert identity.role == "reader"

    def test_basic_token_wrong_password(self, auth_config):
        middleware = BearerAuthMiddleware(
            app=None, auth_config=auth_config,
        )
        token = base64.b64encode(b"bob:wrong").decode()
        identity = middleware._resolve(token)
        assert identity is None

    def test_basic_token_unknown_client(self, auth_config):
        middleware = BearerAuthMiddleware(
            app=None, auth_config=auth_config,
        )
        token = base64.b64encode(b"eve:something").decode()
        identity = middleware._resolve(token)
        assert identity is None

    def test_basic_token_invalid_base64(self, auth_config):
        middleware = BearerAuthMiddleware(
            app=None, auth_config=auth_config,
        )
        identity = middleware._resolve("not-valid-base64!!!")
        assert identity is None

    def test_legacy_mode_static_token(self):
        """Legacy mode: static token match returns ANONYMOUS."""
        middleware = BearerAuthMiddleware(
            app=None, token="my-token",
        )
        identity = middleware._resolve("my-token")
        assert identity == ANONYMOUS

    def test_legacy_mode_wrong_token(self):
        middleware = BearerAuthMiddleware(
            app=None, token="my-token",
        )
        identity = middleware._resolve("wrong")
        assert identity is None


# ===================================================================
# ClientIdentity
# ===================================================================

class TestClientIdentity:

    def test_frozen(self):
        c = ClientIdentity(client_id="claude", role="admin")
        with pytest.raises(AttributeError):
            c.client_id = "chatgpt"  # type: ignore[misc]

    def test_anonymous_default(self):
        assert ANONYMOUS.client_id == ""
        assert ANONYMOUS.role == ""


# ===================================================================
# OAuth: client registration & redirect_uri validation
# ===================================================================

class TestOAuthClientValidation:
    """Regression tests for OAuth client_id / redirect_uri enforcement."""

    @pytest.fixture
    def srv(self):
        pwd_hash = hash_password("alice-pass")
        config = AuthConfig(
            enabled=True,
            roles={"admin": RoleConfig(tools="*", security="none")},
            clients={"alice": ClientConfig(role="admin", password_hash=pwd_hash)},
        )
        return OAuthServer(auth_config=config, db_path=":memory:")

    def _register_client(self, srv, redirect_uris=None):
        """Helper: register a client and return (client_id, redirect_uris)."""
        if redirect_uris is None:
            redirect_uris = ["https://example.com/callback"]
        client_id = "test-client-id"
        srv._store.put_client(client_id, redirect_uris)
        return client_id, redirect_uris

    def _make_form(self, **kwargs):
        """Build a dict that mimics form data."""
        import hashlib, base64, secrets
        verifier = secrets.token_urlsafe(32)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        defaults = {
            "username": "alice",
            "password": "alice-pass",
            "redirect_uri": "https://example.com/callback",
            "client_id": "test-client-id",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "test-state",
        }
        defaults.update(kwargs)
        return defaults

    @pytest.fixture
    def client(self, srv):
        """httpx async client with ASGITransport (avoids TestClient event-loop hang)."""
        import httpx
        from starlette.applications import Starlette

        app = Starlette(routes=srv.routes())
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        return httpx.AsyncClient(transport=transport, base_url="http://testserver")

    @pytest.mark.asyncio
    async def test_unregistered_client_rejected(self, srv, client):
        """Authorization must reject an unknown client_id."""
        form = self._make_form(client_id="not-registered")
        resp = await client.post("/oauth/authorize", data=form, follow_redirects=False)
        assert resp.status_code == 400
        assert "Unknown client_id" in resp.text

    @pytest.mark.asyncio
    async def test_wrong_redirect_uri_rejected(self, srv, client):
        """Authorization must reject a redirect_uri not in the client's registered list."""
        self._register_client(srv, ["https://example.com/callback"])
        form = self._make_form(redirect_uri="https://evil.com/steal")
        resp = await client.post("/oauth/authorize", data=form, follow_redirects=False)
        assert resp.status_code == 400
        assert "redirect_uri not registered" in resp.text

    @pytest.mark.asyncio
    async def test_valid_client_and_redirect_succeeds(self, srv, client):
        """A registered client with matching redirect_uri should get a redirect."""
        self._register_client(srv, ["https://example.com/callback"])
        form = self._make_form()
        resp = await client.post("/oauth/authorize", data=form, follow_redirects=False)
        assert resp.status_code == 302
        assert "code=" in resp.headers["location"]

    @pytest.mark.asyncio
    async def test_pkce_required(self, srv, client):
        """PKCE code_challenge must be present (OAuth 2.1)."""
        self._register_client(srv)
        form = self._make_form(code_challenge="")
        resp = await client.post("/oauth/authorize", data=form, follow_redirects=False)
        assert resp.status_code == 400
        assert "PKCE" in resp.text


# ===================================================================
# tools="*" resolution
# ===================================================================

class TestToolsWildcard:
    """Regression test: tools='*' must resolve to ALL registered tools."""

    def test_tools_wildcard_includes_all_tools(self):
        from mma_mcp.tools import get_registered, evaluate  # noqa: F401
        all_tools = set(get_registered())
        expected = {"evaluate", "evaluate_image"}
        assert expected <= all_tools, f"Missing tools: {expected - all_tools}"

    def test_build_role_runtimes_wildcard(self):
        """_build_role_runtimes with tools='*' must include all registered tools."""
        from mma_mcp.config import AppConfig, AuthConfig, RoleConfig
        from mma_mcp.security.registry import CapabilityRegistry
        from mma_mcp.server import App

        config = AppConfig()
        config.auth = AuthConfig(
            enabled=True,
            roles={"admin": RoleConfig(tools="*", security="none")},
        )
        app = App(config)
        registry = CapabilityRegistry()
        runtimes = app._build_role_runtimes(registry)

        admin_tools = runtimes["admin"].allowed_tools
        assert "evaluate" in admin_tools
        assert "evaluate_image" in admin_tools
