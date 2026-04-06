"""Unit tests for config.py — dataclasses, loading, validation."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from mma_mcp.config import (
    AppConfig,
    AuthConfig,
    ConfigError,
    KernelConfig,
    RoleConfig,
    SecurityConfig,
    ServerConfig,
    ToolsConfig,
    TlsConfig,
    UserConfig,
    _build_auth_config,
    _build_kernel_config,
    _build_security_config,
    _build_server_config,
    _build_tls_config,
    _build_tools_config,
    _validate,
    generate_default_config,
    load_config,
)


# ===================================================================
# Dataclass defaults
# ===================================================================

class TestDefaults:

    def test_kernel_defaults(self):
        k = KernelConfig()
        assert k.mathkernel == ""
        assert k.timeout == 30
        assert k.default_format == "TeXForm"

    def test_server_defaults(self):
        s = ServerConfig()
        assert s.transport == "stdio"
        assert s.host == "127.0.0.1"
        assert s.port == 8000

    def test_security_defaults(self):
        s = SecurityConfig()
        assert s.mode == "blacklist"
        assert "system_exec" in s.deny_groups
        assert s.extra_blocked == []

    def test_tools_defaults(self):
        t = ToolsConfig()
        assert "evaluate" in t.enabled
        assert "solve" in t.enabled

    def test_auth_defaults(self):
        a = AuthConfig()
        assert not a.enabled
        assert a.roles == {}
        assert a.users == {}

    def test_app_config_defaults(self):
        c = AppConfig()
        assert isinstance(c.kernel, KernelConfig)
        assert isinstance(c.auth, AuthConfig)


# ===================================================================
# Config builders
# ===================================================================

class TestConfigBuilders:

    def test_build_kernel_config(self):
        raw = {"kernel": {"timeout": 60, "default_format": "OutputForm"}}
        k = _build_kernel_config(raw)
        assert k.timeout == 60
        assert k.default_format == "OutputForm"

    def test_build_kernel_config_empty(self):
        k = _build_kernel_config({})
        assert k.timeout == 30

    def test_build_server_config(self):
        raw = {"server": {"transport": "http", "port": 9090}}
        s = _build_server_config(raw)
        assert s.transport == "http"
        assert s.port == 9090

    def test_build_tls_config(self):
        raw = {"tls": {"domain": "example.com", "dns_provider": "cloudflare"}}
        t = _build_tls_config(raw)
        assert t.domain == "example.com"
        assert t.dns_provider == "cloudflare"

    def test_build_security_config(self):
        raw = {"security": {"mode": "whitelist", "allow_groups": ["math_core"]}}
        s = _build_security_config(raw)
        assert s.mode == "whitelist"
        assert s.allow_groups == ["math_core"]

    def test_build_tools_config(self):
        raw = {"tools": {"enabled": ["evaluate"]}}
        t = _build_tools_config(raw)
        assert t.enabled == ["evaluate"]

    def test_build_auth_config_disabled(self):
        raw = {}
        a = _build_auth_config(raw)
        assert not a.enabled

    def test_build_auth_config_enabled(self):
        raw = {
            "auth": {
                "enabled": True,
                "roles": {
                    "admin": {"tools": "*", "security": "none"},
                    "reader": {"tools": ["evaluate"]},
                },
                "users": {
                    "alice": {"role": "admin", "password_hash": "scrypt:aa:bb"},
                },
            }
        }
        a = _build_auth_config(raw)
        assert a.enabled
        assert "admin" in a.roles
        assert a.roles["admin"].tools == "*"
        assert a.roles["admin"].security == "none"
        assert "reader" in a.roles
        assert a.roles["reader"].tools == ["evaluate"]
        assert "alice" in a.users
        assert a.users["alice"].role == "admin"


# ===================================================================
# Validation
# ===================================================================

class TestValidation:

    def test_valid_default_config(self):
        """Default config should pass validation."""
        _validate(AppConfig())

    def test_invalid_transport(self):
        c = AppConfig(server=ServerConfig(transport="grpc"))
        with pytest.raises(ConfigError, match="transport"):
            _validate(c)

    def test_invalid_port(self):
        c = AppConfig(server=ServerConfig(port=0))
        with pytest.raises(ConfigError, match="port"):
            _validate(c)

    def test_invalid_security_mode(self):
        c = AppConfig(security=SecurityConfig(mode="custom"))
        with pytest.raises(ConfigError, match="security.mode"):
            _validate(c)

    def test_invalid_default_format(self):
        c = AppConfig(kernel=KernelConfig(default_format="JSONForm"))
        with pytest.raises(ConfigError, match="default_format"):
            _validate(c)

    def test_negative_timeout(self):
        c = AppConfig(kernel=KernelConfig(timeout=-1))
        with pytest.raises(ConfigError, match="timeout"):
            _validate(c)

    def test_tls_enabled_without_domain(self):
        c = AppConfig(tls=TlsConfig(enabled=True, domain=""))
        with pytest.raises(ConfigError, match="domain"):
            _validate(c)

    def test_tls_unknown_dns_provider(self):
        c = AppConfig(tls=TlsConfig(dns_provider="unknownprovider"))
        with pytest.raises(ConfigError, match="dns_provider"):
            _validate(c)

    def test_auth_enabled_no_users(self):
        c = AppConfig(auth=AuthConfig(
            enabled=True,
            roles={"admin": RoleConfig()},
            users={},
        ))
        with pytest.raises(ConfigError, match="no users"):
            _validate(c)

    def test_auth_enabled_no_roles(self):
        c = AppConfig(auth=AuthConfig(
            enabled=True,
            roles={},
            users={"alice": UserConfig(role="admin", password_hash="scrypt:aa:bb")},
        ))
        with pytest.raises(ConfigError, match="no roles"):
            _validate(c)

    def test_auth_user_unknown_role(self):
        c = AppConfig(auth=AuthConfig(
            enabled=True,
            roles={"admin": RoleConfig()},
            users={"alice": UserConfig(role="nonexistent", password_hash="scrypt:aa:bb")},
        ))
        with pytest.raises(ConfigError, match="nonexistent"):
            _validate(c)

    def test_auth_user_bad_password_hash(self):
        c = AppConfig(auth=AuthConfig(
            enabled=True,
            roles={"admin": RoleConfig()},
            users={"alice": UserConfig(role="admin", password_hash="plain:bad")},
        ))
        with pytest.raises(ConfigError, match="password_hash"):
            _validate(c)

    def test_auth_role_bad_security_mode(self):
        c = AppConfig(auth=AuthConfig(
            enabled=True,
            roles={"admin": RoleConfig(security="custom")},
            users={"alice": UserConfig(role="admin", password_hash="scrypt:aa:bb")},
        ))
        with pytest.raises(ConfigError, match="security"):
            _validate(c)


# ===================================================================
# generate_default_config
# ===================================================================

class TestGenerateDefaultConfig:

    def test_generates_file(self, tmp_path):
        target = tmp_path / "mma_mcp.toml"
        result = generate_default_config(target)
        assert result == target
        assert target.exists()
        content = target.read_text()
        assert "[kernel]" in content
        assert "[security]" in content
        assert "[tools]" in content

    def test_generated_config_is_parseable(self, tmp_path):
        """The generated TOML should parse correctly."""
        import tomllib
        target = tmp_path / "mma_mcp.toml"
        generate_default_config(target)
        data = tomllib.loads(target.read_text())
        assert "kernel" in data
        assert "server" in data


# ===================================================================
# load_config (integration with filesystem)
# ===================================================================

class TestLoadConfig:

    def test_no_config_file_returns_defaults(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = load_config()
        assert config.kernel.timeout == 30
        assert config.server.transport == "stdio"

    def test_loads_standalone_toml(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        toml_content = dedent("""\
            [kernel]
            timeout = 99

            [server]
            transport = "http"
            port = 9999

            [security]
            mode = "blacklist"
            deny_groups = ["system_exec"]

            [tools]
            enabled = ["evaluate"]
        """)
        (tmp_path / "mma_mcp.toml").write_text(toml_content)
        config = load_config()
        assert config.kernel.timeout == 99
        assert config.server.transport == "http"
        assert config.server.port == 9999
        assert config.tools.enabled == ["evaluate"]
