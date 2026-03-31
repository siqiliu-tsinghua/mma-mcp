"""Expression-level security filter.

Extracts symbol references from a WL expression string using a regex tokenizer,
then checks the resulting set against the active policy (blacklist or whitelist).

wolframclient provides no WL text parser, so we use a two-pass approach:
  1. Strip string literals to avoid false positives inside quoted text.
  2. Extract identifier tokens (WL symbols match [A-Za-z$][A-Za-z0-9$]*).

Edge cases handled:
  - Symbol["Run"]  → explicit regex match, string argument treated as symbol name.
  - Context-qualified names like System`Run → short name extracted.
"""

from __future__ import annotations

import re
from typing import Literal

# Matches WL string literals (handles escaped quotes inside)
_RE_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')

# Matches context-qualified or plain WL symbol identifiers
# e.g. "System`Run", "Run", "$HomeDirectory", "Global`myFunc"
_RE_SYMBOL = re.compile(r'[A-Za-z$][A-Za-z0-9$]*(?:`[A-Za-z$][A-Za-z0-9$]*)*')

# Matches Symbol["SomeName"] — dynamic symbol construction
_RE_SYMBOL_CALL = re.compile(r'\bSymbol\s*\[\s*"([A-Za-z$][A-Za-z0-9$]*)"\s*\]')

# Matches << operator (syntactic sugar for Get["file"])
_RE_GET_OPERATOR = re.compile(r'<<')


class SecurityError(ValueError):
    """Raised when an expression violates the active security policy."""


def _short_name(sym: str) -> str:
    """Return the unqualified name: 'System`Run' -> 'Run'."""
    return sym.rsplit("`", 1)[-1]


def extract_symbols(expr: str) -> set[str]:
    """Return the set of symbol short-names referenced in *expr*.

    String literal contents are excluded to avoid false positives.
    Symbol["Name"] patterns are treated as direct symbol references.
    """
    # Collect Symbol["X"] references before stripping strings
    dynamic = {m.group(1) for m in _RE_SYMBOL_CALL.finditer(expr)}

    # << operator is syntactic sugar for Get — inject "Get" into symbol set
    if _RE_GET_OPERATOR.search(expr):
        dynamic.add("Get")

    # Strip string literals so their contents don't pollute symbol extraction
    stripped = _RE_STRING.sub('""', expr)

    # Extract all identifier tokens, keep short (unqualified) names
    symbols = {_short_name(m.group()) for m in _RE_SYMBOL.finditer(stripped)}

    return symbols | dynamic


class ExpressionFilter:
    """Checks a WL expression string against a security policy.

    Args:
        mode:    "blacklist" or "whitelist"
        policy:  The active symbol set.
                 - blacklist mode: symbols that are *forbidden*
                 - whitelist mode: symbols that are *allowed*
    """

    def __init__(
        self,
        mode: Literal["blacklist", "whitelist"],
        policy: frozenset[str],
    ) -> None:
        self._mode = mode
        self._policy = policy

    @property
    def mode(self) -> str:
        return self._mode

    def check(self, expr_str: str) -> None:
        """Raise SecurityError if *expr_str* violates the policy."""
        used = extract_symbols(expr_str)

        if self._mode == "blacklist":
            blocked = used & self._policy
            if blocked:
                raise SecurityError(
                    f"Expression contains blocked symbols: {sorted(blocked)}"
                )
        else:  # whitelist
            forbidden = used - self._policy
            if forbidden:
                raise SecurityError(
                    f"Expression contains symbols not in the allowlist: {sorted(forbidden)}"
                )
