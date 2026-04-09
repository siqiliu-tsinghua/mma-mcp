# Contributing to mma-mcp

Thank you for your interest! Contributions of the following types are welcome:

## Areas of Contribution

- **Deployment docs for other platforms**: The current deployment guide covers Debian/Ubuntu only. If you have successfully deployed on RHEL/Fedora, Arch, macOS, or Windows (WSL2), we welcome adapted documentation or scripts.
- **MCP client compatibility reports**: Claude Desktop, Cursor, Windsurf, etc. — we welcome connection guides and known-issue reports.
- **Bug fixes and feature improvements**

## Development Setup

```bash
# Clone the project
git clone https://github.com/liusq7/mma-mcp.git
cd mma-mcp

# Install dependencies (requires uv)
uv sync --all-extras

# Run unit tests (no Wolfram Engine needed)
uv run pytest tests/ -m "not integration" -q

# Run integration tests (requires a local Wolfram Engine)
uv run pytest tests/ -m integration -q
```

## Commit Guidelines

- Keep commit messages concise; describe "why" rather than "what"
- New features should include tests
- Ensure `uv run pytest tests/ -m "not integration" -q` passes before submitting a PR

## Code Style

- Python 3.11+ idioms, type hints where practical
- No unnecessary dependencies — prefer stdlib solutions
- Security-sensitive code requires tests covering bypass scenarios

## Pull Requests

1. Fork the repository and create a feature branch
2. Make your changes with tests
3. Ensure all unit tests pass
4. Submit a PR with a clear description of the change and motivation
