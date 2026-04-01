"""Wolfram kernel lifecycle management.

Maintains a persistent WolframLanguageSession. On evaluation failure due to
kernel crash the session is automatically restarted and the call retried once.
"""

from __future__ import annotations

import base64
import concurrent.futures
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from wolframclient.evaluation import WolframLanguageSession
from wolframclient.exception import WolframKernelException
from wolframclient.language import wl, wlexpr

logger = logging.getLogger(__name__)

# Well-known install locations to search when PATH lookup fails
_KERNEL_CANDIDATES = [
    "/usr/local/bin/WolframKernel",
    "/usr/bin/WolframKernel",
    # Linux default install
    "/usr/local/Wolfram/WolframEngine/*/Executables/WolframKernel",
    "/usr/local/Wolfram/Wolfram/*/Executables/WolframKernel",
    # macOS
    "/Applications/Wolfram Engine.app/Contents/MacOS/WolframKernel",
    "/Applications/Mathematica.app/Contents/MacOS/WolframKernel",
]


def find_kernel(hint: str | None = None) -> str | None:
    """Locate a WolframKernel binary.

    Resolution order:
      1. *hint* (explicit path from config or env var)
      2. ``WOLFRAM_KERNEL`` environment variable
      3. ``shutil.which("WolframKernel")``
      4. ``shutil.which("wolframscript")`` — wolframclient accepts this too
      5. Well-known install paths (glob-expanded)

    Returns the first path that exists, or None.
    """
    import glob

    # 1. explicit hint
    if hint and Path(hint).exists():
        return hint

    # 2. env var
    env = os.environ.get("WOLFRAM_KERNEL")
    if env and Path(env).exists():
        return env

    # 3-4. PATH lookup
    for name in ("WolframKernel", "wolframscript"):
        found = shutil.which(name)
        if found:
            return found

    # 5. well-known locations (glob for version wildcards)
    for pattern in _KERNEL_CANDIDATES:
        for match in sorted(glob.glob(pattern), reverse=True):  # newest first
            if Path(match).exists():
                return match

    return None


def find_wolframscript(hint: str | None = None) -> str | None:
    """Locate wolframscript binary.

    Resolution order:
      1. *hint* (explicit path from config)
      2. ``shutil.which("wolframscript")``

    Returns the first path that exists, or None.
    """
    if hint and Path(hint).exists():
        return hint
    return shutil.which("wolframscript")


def _ensure_display() -> None:
    """Ensure a DISPLAY is available for graphics rendering.

    If DISPLAY is not set and Xvfb is installed, starts a virtual framebuffer
    on :99 and sets DISPLAY accordingly.  No-op if DISPLAY is already set or
    Xvfb is not installed.
    """
    import time

    # WolframNB uses Qt for rendering; in headless environments the xcb
    # plugin may fail even with Xvfb.  "offscreen" works reliably.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    if os.environ.get("DISPLAY"):
        return
    if not shutil.which("Xvfb"):
        logger.warning(
            "No DISPLAY set and Xvfb not found — graphics export may hang. "
            "Install xvfb: sudo apt-get install -y xvfb"
        )
        return
    display = ":99"
    # Check if Xvfb is already running on this display
    lock = Path(f"/tmp/.X{display[1:]}-lock")
    if lock.exists():
        logger.info("Xvfb already running on %s", display)
    else:
        try:
            subprocess.Popen(
                ["Xvfb", display, "-screen", "0", "1280x1024x24"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Wait for Xvfb to be ready (lock file appears)
            for _ in range(20):
                if lock.exists():
                    break
                time.sleep(0.1)
            logger.info("Started Xvfb on %s", display)
        except Exception:
            logger.warning("Failed to start Xvfb", exc_info=True)
            return
    os.environ["DISPLAY"] = display


class KernelTimeout(Exception):
    """Raised when the kernel does not respond within the hard timeout."""


class KernelSession:
    """Wraps a single WolframLanguageSession with auto-restart on crash."""

    def __init__(self, kernel: str | None = None) -> None:
        """
        Args:
            kernel: Path to WolframKernel binary. None = auto-detect via
                    find_kernel() (env var → PATH → well-known locations).
        """
        self._kernel = kernel or find_kernel()
        self._session: WolframLanguageSession | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._session is not None:
            return
        _ensure_display()
        logger.info("Starting Wolfram kernel session (kernel=%s)", self._kernel or "auto")
        self._session = self._make_session()
        self._session.start()
        logger.info("Wolfram kernel ready")

    def stop(self) -> None:
        if self._session is None:
            return
        logger.info("Stopping Wolfram kernel session")
        try:
            self._session.stop()
        except Exception:
            pass
        self._session = None

    def restart(self) -> None:
        logger.warning("Restarting Wolfram kernel session")
        self.stop()
        self.start()

    def _make_session(self) -> WolframLanguageSession:
        if self._kernel:
            return WolframLanguageSession(self._kernel)
        return WolframLanguageSession()

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(self, expr: Any, *, retry: bool = True, hard_timeout: int = 0) -> Any:
        """Evaluate a WL expression. Returns the Python-converted result.

        Accepts anything wolframclient accepts: wl.* objects, wlexpr strings,
        or raw WL expression objects.

        Args:
            retry:        Retry once after kernel crash.
            hard_timeout: Python-side hard timeout in seconds. If the kernel
                          does not respond in time, it is force-restarted and
                          ``KernelTimeout`` is raised. 0 = no limit.
        """
        self._ensure_started()
        try:
            return self._evaluate_with_hard_timeout(expr, hard_timeout)
        except WolframKernelException:
            if retry:
                logger.warning("Kernel exception — restarting and retrying once")
                self.restart()
                return self._evaluate_with_hard_timeout(expr, hard_timeout)
            raise

    def _evaluate_with_hard_timeout(self, expr: Any, hard_timeout: int) -> Any:
        """Run evaluation, with optional thread-based hard timeout."""
        if hard_timeout <= 0:
            return self._session.evaluate(expr)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self._session.evaluate, expr)
            try:
                return future.result(timeout=hard_timeout)
            except concurrent.futures.TimeoutError:
                logger.error(
                    "Kernel did not respond within %d seconds — force-restarting",
                    hard_timeout,
                )
                self.restart()
                raise KernelTimeout(
                    f"Kernel did not respond within {hard_timeout} seconds "
                    f"and was force-restarted"
                )

    def evaluate_to_string(
        self, expr_str: str, form: str = "TeXForm",
        timeout: int = 0, hard_timeout: int = 0, context: str = "",
    ) -> str:
        """Evaluate a WL expression string and return the result as a string.

        Args:
            expr_str:     Wolfram Language expression.
            form:         Output format (TeXForm, OutputForm, InputForm, …).
            timeout:      WL-side TimeConstrained seconds. 0 = no limit.
            hard_timeout: Python-side hard timeout seconds. 0 = no limit.
            context:      WL context for session isolation (e.g. "MCP$alice`").
        """
        inner = _wrap_context(expr_str, context)
        if timeout > 0:
            inner = f"TimeConstrained[{inner}, {timeout}]"
        wrapped = wl.ToString(wlexpr(inner), wlexpr(form))
        result = self.evaluate(wrapped, hard_timeout=hard_timeout)
        if isinstance(result, str):
            return result
        return str(result)

    def evaluate_to_image(
        self, expr_str: str, timeout: int = 0, hard_timeout: int = 0,
        context: str = "",
    ) -> bytes:
        """Evaluate a WL expression and export the result as PNG bytes.

        Wraps the expression in Rasterize so that any Graphics/Plot output
        is captured even if the expression is not inherently graphical.

        Args:
            expr_str:     Wolfram Language expression.
            timeout:      WL-side TimeConstrained seconds. 0 = no limit.
            hard_timeout: Python-side hard timeout seconds. 0 = no limit.
            context:      WL context for session isolation.
        """
        self._ensure_started()
        inner = _wrap_context(expr_str, context)
        if timeout > 0:
            inner = f"TimeConstrained[{inner}, {timeout}]"

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name

        export_expr = wl.Export(
            tmp_path,
            wl.Rasterize(wlexpr(inner), wlexpr('ImageResolution -> 144')),
            "PNG",
        )
        self.evaluate(export_expr, hard_timeout=hard_timeout)
        data = Path(tmp_path).read_bytes()
        Path(tmp_path).unlink(missing_ok=True)
        return data

    def evaluate_to_image_b64(self, expr_str: str) -> str:
        """Like evaluate_to_image but returns a base64-encoded string."""
        return base64.b64encode(self.evaluate_to_image(expr_str)).decode()

    def get_all_system_symbols(self) -> set[str]:
        """Return all symbol names in the System` context.

        Used by the security layer to build the whitelist at startup.
        """
        result = self.evaluate(wl.Names("System`*"))
        return set(result)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_started(self) -> None:
        if self._session is None:
            self.start()

    # Context manager support
    def __enter__(self) -> "KernelSession":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# Session isolation helper
# ---------------------------------------------------------------------------

def _wrap_context(expr_str: str, context: str) -> str:
    """Wrap an expression so it is parsed and evaluated in an isolated context.

    Uses ``ToExpression`` inside a ``Block`` so that symbol resolution
    happens *after* ``$Context`` / ``$ContextPath`` have been changed.
    Without ``ToExpression``, ``wlexpr`` would parse ``x`` as ``Global`x``
    before the ``Block`` takes effect.

    The ``ToExpression`` here is internal infrastructure — the user's
    expression has already passed security filtering before this point.

    If *context* is empty, returns *expr_str* unchanged.
    """
    if not context:
        return expr_str
    escaped = _escape_for_wl_string(expr_str)
    return (
        f'Block[{{$Context = "{context}", '
        f'$ContextPath = {{"{context}", "System`"}}}}, '
        f'ToExpression["{escaped}"]]'
    )


def _escape_for_wl_string(s: str) -> str:
    """Escape a string for embedding in WL double-quoted string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def sanitize_context_name(username: str) -> str:
    """Convert a username to a valid WL context name.

    Only keeps ASCII letters, digits, and ``$``; prepends ``MCP$`` and
    appends the context delimiter backtick.

    Example: ``"alice"`` → ``"MCP$alice`"``
    """
    safe = "".join(c for c in username if c.isalnum() or c == "$")
    if not safe:
        safe = "anonymous"
    return f"MCP${safe}`"
