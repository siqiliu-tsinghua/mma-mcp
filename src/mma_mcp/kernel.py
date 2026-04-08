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
import tempfile
import threading
import time
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


class KernelTimeout(Exception):
    """Raised when the kernel does not respond within the hard timeout."""


class KernelSession:
    """Wraps a single WolframLanguageSession with auto-restart on crash."""

    def __init__(
        self,
        kernel: str | None = None,
        health_check_interval: int = 0,
        idle_timeout: int = 0,
    ) -> None:
        """
        Args:
            kernel:   Path to WolframKernel binary. None = auto-detect.
            health_check_interval: Seconds between health pings. 0 = disabled.
            idle_timeout: Stop kernel after N seconds idle. 0 = never.
        """
        self._kernel = kernel or find_kernel()
        self._health_check_interval = health_check_interval
        self._idle_timeout = idle_timeout
        self._lock = threading.Lock()
        self._session: WolframLanguageSession | None = None
        self._last_activity: float = 0.0
        self._health_thread: threading.Thread | None = None
        self._health_stop = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._session is not None:
                return
            logger.info("Starting Wolfram kernel session (kernel=%s)", self._kernel or "auto")
            self._session = self._make_session()
            self._session.start()
            self._last_activity = time.monotonic()
            logger.info("Wolfram kernel ready")
            self._start_health_thread()

    def stop(self) -> None:
        self._stop_health_thread()
        with self._lock:
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
        self._stop_health_thread()
        with self._lock:
            if self._session is not None:
                try:
                    self._session.stop()
                except Exception:
                    pass
                self._session = None
        self.start()

    def _make_session(self) -> WolframLanguageSession:
        if self._kernel:
            return WolframLanguageSession(self._kernel)
        return WolframLanguageSession()

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def _start_health_thread(self) -> None:
        """Start background health-check thread if configured."""
        if self._health_check_interval <= 0 and self._idle_timeout <= 0:
            return
        self._health_stop.clear()
        self._health_thread = threading.Thread(
            target=self._health_loop, daemon=True, name="kernel-health",
        )
        self._health_thread.start()
        logger.info(
            "Health check started (ping=%ds, idle=%ds)",
            self._health_check_interval, self._idle_timeout,
        )

    def _stop_health_thread(self) -> None:
        thread = self._health_thread
        if thread is not None:
            self._health_stop.set()
            thread.join(timeout=5)
            self._health_thread = None

    def _health_loop(self) -> None:
        """Periodically ping the kernel and check idle timeout."""
        interval = self._health_check_interval if self._health_check_interval > 0 else 30
        while not self._health_stop.wait(timeout=interval):
            if self._session is None:
                continue

            # Idle timeout check
            if self._idle_timeout > 0:
                idle_secs = time.monotonic() - self._last_activity
                if idle_secs >= self._idle_timeout:
                    logger.info(
                        "Kernel idle for %.0fs (limit %ds) — stopping",
                        idle_secs, self._idle_timeout,
                    )
                    self._stop_session_only()
                    continue

            # Health ping
            if self._health_check_interval > 0:
                try:
                    result = self._session.evaluate(wlexpr("1+1"))
                    if result != 2:
                        logger.warning("Health check got unexpected result: %r — restarting", result)
                        self.restart()
                except Exception:
                    logger.warning("Health check failed — restarting kernel", exc_info=True)
                    self.restart()

    def _stop_session_only(self) -> None:
        """Stop the kernel session without stopping the health thread."""
        with self._lock:
            if self._session is None:
                return
            try:
                self._session.stop()
            except Exception:
                pass
            self._session = None

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
        self._last_activity = time.monotonic()
        try:
            result = self._evaluate_with_hard_timeout(expr, hard_timeout)
            self._last_activity = time.monotonic()
            return result
        except WolframKernelException:
            if retry:
                logger.warning("Kernel exception — restarting and retrying once")
                self.restart()
                return self._evaluate_with_hard_timeout(expr, hard_timeout)
            raise

    def _evaluate_with_hard_timeout(self, expr: Any, hard_timeout: int) -> Any:
        """Run evaluation, with optional thread-based hard timeout."""
        session = self._session
        if session is None:
            raise WolframKernelException("Kernel session is not running")
        if hard_timeout <= 0:
            return session.evaluate(expr)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(session.evaluate, expr)
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
        try:
            self.evaluate(export_expr, hard_timeout=hard_timeout)
            data = Path(tmp_path).read_bytes()
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        return data

    def evaluate_to_image_b64(self, expr_str: str) -> str:
        """Like evaluate_to_image but returns a base64-encoded string."""
        return base64.b64encode(self.evaluate_to_image(expr_str)).decode()

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


def sanitize_context_name(client_id: str) -> str:
    """Convert a client identifier to a valid WL context name.

    Only keeps ASCII letters, digits, and ``$``; prepends ``MCP$`` and
    appends the context delimiter backtick.

    Example: ``"claude"`` → ``"MCP$claude`"``
    """
    safe = "".join(c for c in client_id if c.isalnum() or c == "$")
    if not safe:
        safe = "anonymous"
    return f"MCP${safe}`"
