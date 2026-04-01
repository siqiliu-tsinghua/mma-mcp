"""Unit tests for auth.py, oauth.py, and passwords.py."""

from __future__ import annotations

import base64

import pytest

from mma_mcp.auth import ANONYMOUS, BearerAuthMiddleware, UserIdentity, current_user
from mma_mcp.config import AuthConfig, RoleConfig, UserConfig
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
        srv = OAuthServer(password="my-secret")
        assert srv.validate_token("my-secret")
        assert not srv.validate_token("wrong")

    def test_get_token_user_returns_none_legacy(self):
        srv = OAuthServer(password="my-secret")
        assert srv.get_token_user("anything") is None

    def test_multi_user_is_false(self):
        srv = OAuthServer(password="my-secret")
        assert not srv.multi_user


# ===================================================================
# OAuthServer — multi-user mode
# ===================================================================

class TestOAuthServerMultiUser:

    @pytest.fixture
    def auth_config(self):
        pwd_hash = hash_password("alice-pass")
        return AuthConfig(
            enabled=True,
            roles={"admin": RoleConfig(tools="*", security="none")},
            users={"alice": UserConfig(role="admin", password_hash=pwd_hash)},
        )

    @pytest.fixture
    def srv(self, auth_config):
        return OAuthServer(auth_config=auth_config)

    def test_multi_user_is_true(self, srv):
        assert srv.multi_user

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
            users={"bob": UserConfig(role="reader", password_hash=pwd_hash)},
        )

    def test_basic_token_valid(self, auth_config):
        """base64(username:password) resolves to correct identity."""
        middleware = BearerAuthMiddleware(
            app=None, auth_config=auth_config,
        )
        token = base64.b64encode(b"bob:bob-pass").decode()
        identity = middleware._resolve(token)
        assert identity is not None
        assert identity.username == "bob"
        assert identity.role == "reader"

    def test_basic_token_wrong_password(self, auth_config):
        middleware = BearerAuthMiddleware(
            app=None, auth_config=auth_config,
        )
        token = base64.b64encode(b"bob:wrong").decode()
        identity = middleware._resolve(token)
        assert identity is None

    def test_basic_token_unknown_user(self, auth_config):
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
# UserIdentity
# ===================================================================

class TestUserIdentity:

    def test_frozen(self):
        u = UserIdentity(username="alice", role="admin")
        with pytest.raises(AttributeError):
            u.username = "bob"  # type: ignore[misc]

    def test_anonymous_default(self):
        assert ANONYMOUS.username == ""
        assert ANONYMOUS.role == ""
