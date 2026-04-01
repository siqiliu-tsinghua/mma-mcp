"""Capability registry: loads group JSON files and resolves security policy.

Usage:
    from mma_mcp.config import SecurityConfig

    registry = CapabilityRegistry()
    registry.initialize_system_symbols(symbols)   # optional, once at startup
    filt = registry.build_filter(config)           # build ExpressionFilter
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from mma_mcp.config import SecurityConfig

from .filter import ExpressionFilter

logger = logging.getLogger(__name__)

_GROUPS_DIR = Path(__file__).parent / "groups"

# Default blacklist-mode groups — always blocked unless explicitly overridden
_DEFAULT_BLACKLIST_GROUPS = [
    "system_exec",
    "dynamic_eval",
    "file_read",
    "file_write",
    "networking",
    "external_services",
]

# Default whitelist-mode groups — allowed out of the box
_DEFAULT_WHITELIST_GROUPS = [
    "arithmetic",
    "algebra",
    "calculus",
    "linear_algebra",
    "statistics",
    "number_theory",
    "special_functions",
    "combinatorics",
    "list_ops",
    "string_ops",
    "programming",
    "plotting_2d",
    "plotting_3d",
    "graphics",
]

# All dangerous group names (used to subtract from whitelist)
_DANGEROUS_GROUPS = [
    "system_exec", "dynamic_eval", "file_write",
    "networking", "external_services", "file_read",
]


class CapabilityRegistry:
    """Loads group definitions from JSON files and builds ExpressionFilter instances."""

    def __init__(self, groups_dir: Path | None = None) -> None:
        self._groups_dir = groups_dir or _GROUPS_DIR
        self._groups: dict[str, frozenset[str]] = {}
        self._all_system_symbols: frozenset[str] = frozenset()
        self._load_groups()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _load_groups(self) -> None:
        if not self._groups_dir.is_dir():
            logger.warning("Groups directory not found: %s", self._groups_dir)
            return
        for path in sorted(self._groups_dir.glob("*.json")):
            if path.stem == "manifest":
                continue
            try:
                symbols = json.loads(path.read_text(encoding="utf-8"))
                self._groups[path.stem] = frozenset(symbols)
                logger.debug("Loaded group %s (%d symbols)", path.stem, len(symbols))
            except Exception:
                logger.exception("Failed to load group file: %s", path)

    def initialize_system_symbols(self, all_symbols: set[str]) -> None:
        """Supply the full set of System` symbols (queried from kernel once at startup)."""
        self._all_system_symbols = frozenset(all_symbols)
        logger.info("Initialized with %d system symbols", len(self._all_system_symbols))

    # ------------------------------------------------------------------
    # Policy resolution
    # ------------------------------------------------------------------

    def build_filter(self, config: SecurityConfig) -> ExpressionFilter:
        """Return an ExpressionFilter configured according to *config*."""
        if config.mode == "blacklist":
            return self._build_blacklist_filter(config)
        else:
            return self._build_whitelist_filter(config)

    def _build_blacklist_filter(self, config: SecurityConfig) -> ExpressionFilter:
        groups = config.deny_groups if config.deny_groups else _DEFAULT_BLACKLIST_GROUPS
        blocked: set[str] = set()
        for name in groups:
            blocked |= self._resolve_group(name)
        blocked |= set(config.extra_blocked)
        logger.info(
            "Blacklist policy: %d blocked symbols from groups %s", len(blocked), groups
        )
        return ExpressionFilter("blacklist", frozenset(blocked))

    def _build_whitelist_filter(self, config: SecurityConfig) -> ExpressionFilter:
        # Dangerous symbols = union of all dangerous groups + extra_blocked
        dangerous: set[str] = set()
        for name in _DANGEROUS_GROUPS:
            dangerous |= self._resolve_group(name)
        dangerous |= set(config.extra_blocked)

        # Allowed = explicitly configured groups (or defaults) + extra_allowed
        groups = config.allow_groups if config.allow_groups else _DEFAULT_WHITELIST_GROUPS
        allowed: set[str] = set()
        for name in groups:
            allowed |= self._resolve_group(name)

        # If we have system symbols from the kernel, fall back to
        # all_system_symbols minus dangerous for any group not found locally
        if self._all_system_symbols:
            allowed |= self._all_system_symbols - dangerous

        allowed |= set(config.extra_allowed)
        allowed -= dangerous  # extra_blocked always wins

        logger.info(
            "Whitelist policy: %d allowed symbols from groups %s", len(allowed), groups
        )
        return ExpressionFilter("whitelist", frozenset(allowed))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_group(self, name: str) -> frozenset[str]:
        if name not in self._groups:
            logger.warning("Unknown capability group: %r", name)
            return frozenset()
        return self._groups[name]

    def available_groups(self) -> list[str]:
        return sorted(self._groups.keys())
