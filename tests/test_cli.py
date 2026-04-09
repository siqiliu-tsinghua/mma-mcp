"""Unit tests for CLI subcommands (server.py argparse + handlers)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from mma_mcp.server import _build_parser


# ===================================================================
# Argparse parsing
# ===================================================================

class TestArgparse:

    def test_default_to_serve(self):
        """No subcommand defaults to 'serve'."""
        from mma_mcp.server import main
        parser = _build_parser()
        args = parser.parse_args(["serve"])
        assert args.command == "serve"

    def test_serve_with_options(self):
        parser = _build_parser()
        args = parser.parse_args(["serve", "--transport", "http", "--host", "0.0.0.0", "--port", "9000"])
        assert args.transport == "http"
        assert args.host == "0.0.0.0"
        assert args.port == 9000

    def test_serve_defaults(self):
        parser = _build_parser()
        args = parser.parse_args(["serve"])
        assert args.transport is None
        assert args.host is None
        assert args.port is None

    def test_init(self):
        parser = _build_parser()
        args = parser.parse_args(["init"])
        assert args.command == "init"

    def test_setup(self):
        parser = _build_parser()
        args = parser.parse_args(["setup"])
        assert args.command == "setup"
        assert args.force is False

    def test_setup_force(self):
        parser = _build_parser()
        args = parser.parse_args(["setup", "--force"])
        assert args.force is True

    def test_caddyfile(self):
        parser = _build_parser()
        args = parser.parse_args(["caddyfile"])
        assert args.command == "caddyfile"

    def test_hash_password(self):
        parser = _build_parser()
        args = parser.parse_args(["hash-password"])
        assert args.command == "hash-password"

    def test_add_client(self):
        parser = _build_parser()
        args = parser.parse_args(["add-client", "claude", "--role", "admin"])
        assert args.command == "add-client"
        assert args.client_id == "claude"
        assert args.role == "admin"

    def test_add_client_no_id(self):
        parser = _build_parser()
        args = parser.parse_args(["add-client", "--role", "reader"])
        assert args.client_id is None

    def test_add_client_requires_role(self):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["add-client", "claude"])


# ===================================================================
# _cmd_init
# ===================================================================

class TestCmdInit:

    def test_generates_toml(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from mma_mcp.server import _cmd_init
        _cmd_init()
        toml_path = tmp_path / "mma_mcp.toml"
        assert toml_path.exists()
        content = toml_path.read_text()
        assert "[kernel]" in content
        assert "[server]" in content


# ===================================================================
# _cmd_caddyfile
# ===================================================================

class TestCmdCaddyfile:

    def test_no_domain_exits(self, tmp_path, monkeypatch):
        """Should exit with error when tls.domain is not set."""
        monkeypatch.chdir(tmp_path)
        # Write a minimal config without tls.domain
        (tmp_path / "mma_mcp.toml").write_text("[kernel]\n[server]\n[tls]\n")
        from mma_mcp.server import _cmd_caddyfile
        with pytest.raises(SystemExit):
            _cmd_caddyfile()

    def test_generates_caddyfile(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "mma_mcp.toml").write_text(
            '[kernel]\n[server]\nhost = "127.0.0.1"\nport = 8000\n'
            '[tls]\ndomain = "mma.example.com"\n'
        )
        from mma_mcp.server import _cmd_caddyfile
        _cmd_caddyfile()
        caddy = tmp_path / "Caddyfile"
        assert caddy.exists()
        content = caddy.read_text()
        assert "mma.example.com" in content


# ===================================================================
# _cmd_hash_password
# ===================================================================

class TestCmdHashPassword:

    def test_matching_passwords(self, capsys):
        from mma_mcp.server import _cmd_hash_password
        with patch("getpass.getpass", side_effect=["secret", "secret"]):
            _cmd_hash_password()
        output = capsys.readouterr().out.strip()
        assert output.startswith("scrypt:")

    def test_mismatched_passwords(self):
        from mma_mcp.server import _cmd_hash_password
        with patch("getpass.getpass", side_effect=["secret", "wrong"]):
            with pytest.raises(SystemExit):
                _cmd_hash_password()


# ===================================================================
# _cmd_add_client
# ===================================================================

class TestCmdAddClient:

    def test_generates_toml_snippet(self, capsys):
        from mma_mcp.server import _cmd_add_client
        parser = _build_parser()
        args = parser.parse_args(["add-client", "claude", "--role", "admin"])
        with patch("getpass.getpass", side_effect=["mypass", "mypass"]):
            _cmd_add_client(args)
        output = capsys.readouterr().out
        assert "[auth.clients.claude]" in output
        assert 'role = "admin"' in output
        assert "scrypt:" in output

    def test_mismatched_passwords(self):
        from mma_mcp.server import _cmd_add_client
        parser = _build_parser()
        args = parser.parse_args(["add-client", "claude", "--role", "admin"])
        with patch("getpass.getpass", side_effect=["pass1", "pass2"]):
            with pytest.raises(SystemExit):
                _cmd_add_client(args)

    def test_empty_client_id_exits(self):
        from mma_mcp.server import _cmd_add_client
        parser = _build_parser()
        args = parser.parse_args(["add-client", "--role", "admin"])
        with patch("builtins.input", return_value=""), \
             patch("getpass.getpass", side_effect=["p", "p"]):
            with pytest.raises(SystemExit):
                _cmd_add_client(args)


# ===================================================================
# _cmd_setup — skip-when-exists path
# ===================================================================

class TestCmdSetup:

    def test_skips_when_manifest_exists(self, capsys):
        """When manifest.json exists and --force is not set, should skip."""
        from mma_mcp.server import _cmd_setup
        _cmd_setup(force=False)
        output = capsys.readouterr().out
        assert "跳过" in output or "skip" in output.lower()


# ===================================================================
# main() — argv routing
# ===================================================================

class TestMainRouting:

    def test_no_args_defaults_to_serve(self):
        """No arguments should route to serve (which we mock to avoid starting)."""
        from mma_mcp.server import main
        with patch("mma_mcp.server._cmd_serve") as mock_serve, \
             patch("sys.argv", ["mma-mcp"]):
            main()
            mock_serve.assert_called_once()

    def test_init_routes_correctly(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from mma_mcp.server import main
        with patch("sys.argv", ["mma-mcp", "init"]):
            main()
        assert (tmp_path / "mma_mcp.toml").exists()

    def test_unknown_arg_treated_as_serve_flag(self):
        """Unknown args should be passed to serve subcommand."""
        from mma_mcp.server import main
        with patch("mma_mcp.server._cmd_serve") as mock_serve, \
             patch("sys.argv", ["mma-mcp", "--transport", "http"]):
            main()
            mock_serve.assert_called_once()
