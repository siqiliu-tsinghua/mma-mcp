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


_display_available: bool = False

# System packages required by WolframNB (Qt-based renderer)
_GRAPHICS_DEPS = "xvfb libfontconfig1 fonts-dejavu-core libxkbcommon0 libegl1"
_GRAPHICS_INSTALL_HINT = f"sudo apt-get install -y {_GRAPHICS_DEPS}  (Debian/Ubuntu)"


def display_available() -> bool:
    """Return True if a DISPLAY has been set up for graphics rendering."""
    return _display_available


def _start_xvfb() -> str | None:
    """Start Xvfb on :99 if not already running. Returns display string or None."""
    import time

    display = ":99"
    lock = Path(f"/tmp/.X{display[1:]}-lock")
    if lock.exists():
        logger.info("Xvfb already running on %s", display)
        return display
    try:
        proc = subprocess.Popen(
            ["Xvfb", display, "-screen", "0", "1280x1024x24"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(20):
            if lock.exists():
                break
            if proc.poll() is not None:
                logger.warning("Xvfb exited immediately with code %d", proc.returncode)
                return None
            time.sleep(0.1)
        if not lock.exists():
            logger.warning("Xvfb lock file %s never appeared", lock)
            return None
        logger.info("Started Xvfb on %s", display)
        return display
    except Exception:
        logger.warning("Failed to start Xvfb", exc_info=True)
        return None


def _ensure_display(graphics_mode: str = "auto") -> None:
    """Set up display environment for graphics rendering.

    Args:
        graphics_mode: ``"auto"`` (detect), ``"display"`` (trust existing
            DISPLAY), ``"xvfb"`` (require Xvfb), ``"none"`` (skip).
    """
    global _display_available

    # WolframNB is Qt-based; offscreen plugin avoids xcb failures in headless envs
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    if graphics_mode == "none":
        logger.info("Graphics disabled by configuration (kernel.graphics = 'none')")
        return

    # "display" mode: trust whatever DISPLAY is already set
    if graphics_mode == "display":
        if os.environ.get("DISPLAY"):
            _display_available = True
        else:
            logger.error(
                "kernel.graphics = 'display' but DISPLAY is not set. "
                "Falling back to Xvfb.",
            )
            # Fall through to Xvfb attempt
            graphics_mode = "auto"
        if _display_available:
            return

    # Already have a display (auto/xvfb modes)
    if os.environ.get("DISPLAY"):
        _display_available = True
        return

    if not shutil.which("Xvfb"):
        if graphics_mode == "xvfb":
            logger.error(
                "kernel.graphics = 'xvfb' but Xvfb not found. "
                "Install (Debian/Ubuntu): %s", _GRAPHICS_INSTALL_HINT,
            )
        else:
            logger.warning(
                "No DISPLAY and Xvfb not found — graphics unavailable. "
                "Install (Debian/Ubuntu): %s", _GRAPHICS_INSTALL_HINT,
            )
        return

    display = _start_xvfb()
    if display:
        os.environ["DISPLAY"] = display
        _display_available = True
    elif graphics_mode == "xvfb":
        logger.error("kernel.graphics = 'xvfb' but failed to start Xvfb")


class KernelTimeout(Exception):
    """Raised when the kernel does not respond within the hard timeout."""


class KernelSession:
    """Wraps a single WolframLanguageSession with auto-restart on crash."""

    def __init__(
        self,
        kernel: str | None = None,
        graphics: str = "auto",
        health_check_interval: int = 0,
        idle_timeout: int = 0,
    ) -> None:
        """
        Args:
            kernel:   Path to WolframKernel binary. None = auto-detect.
            graphics: Graphics mode — ``"auto"``, ``"xvfb"``, or ``"none"``.
            health_check_interval: Seconds between health pings. 0 = disabled.
            idle_timeout: Stop kernel after N seconds idle. 0 = never.
        """
        self._kernel = kernel or find_kernel()
        self._graphics = graphics
        self._health_check_interval = health_check_interval
        self._idle_timeout = idle_timeout
        self._session: WolframLanguageSession | None = None
        self._last_activity: float = 0.0
        self._health_thread: threading.Thread | None = None
        self._health_stop = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._session is not None:
            return
        _ensure_display(self._graphics)
        logger.info("Starting Wolfram kernel session (kernel=%s)", self._kernel or "auto")
        self._session = self._make_session()
        self._session.start()
        self._last_activity = time.monotonic()
        logger.info("Wolfram kernel ready")
        self._start_health_thread()

    def stop(self) -> None:
        self._stop_health_thread()
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
        if self._health_thread is not None:
            self._health_stop.set()
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
        if not _display_available:
            raise RuntimeError(
                "Graphics rendering unavailable: no DISPLAY set and Xvfb not found. "
                "Install xvfb: sudo apt-get install -y xvfb libfontconfig1 "
                "fonts-dejavu-core libxkbcommon0 libegl1"
            )
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


# ---------------------------------------------------------------------------
# Graphics capability check (used by `mma-mcp setup`)
# ---------------------------------------------------------------------------

class GraphicsCheckResult:
    """Result of a graphics rendering capability test."""

    __slots__ = ("ok", "mode", "message", "missing_deps")

    def __init__(
        self, ok: bool, mode: str, message: str, missing_deps: list[str],
    ) -> None:
        self.ok = ok
        self.mode = mode          # "display", "xvfb", or "none"
        self.message = message    # human-readable summary
        self.missing_deps = missing_deps


def _render_test_plot(kernel_path: str | None) -> tuple[bool, int]:
    """Render a small test plot. Returns (success, png_size)."""
    ks = KernelSession(kernel=kernel_path, graphics="none")  # skip _ensure_display
    ks.start()
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        export_expr = wl.Export(
            tmp,
            wl.Rasterize(
                wlexpr('Plot[Sin[x], {x, 0, 2 Pi}]'),
                wlexpr('ImageResolution -> 72'),
            ),
            "PNG",
        )
        ks.evaluate(export_expr, hard_timeout=30)
        data = Path(tmp).read_bytes()
        Path(tmp).unlink(missing_ok=True)
        return (data[:4] == b"\x89PNG" and len(data) > 500), len(data)
    finally:
        ks.stop()


def check_graphics(kernel_path: str | None = None) -> GraphicsCheckResult:
    """Test whether graphics rendering works end-to-end.

    Detection order:
      1. If DISPLAY is already set (native X11, Wayland, VNC, etc.), try
         rendering with it.  Success → mode="display".
      2. Otherwise, check for Xvfb + shared libs, start Xvfb, try rendering.
         Success → mode="xvfb".
      3. Everything else → mode="none".

    Returns a ``GraphicsCheckResult`` with the diagnosis.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    # ------------------------------------------------------------------
    # 1. Try existing DISPLAY first
    # ------------------------------------------------------------------
    existing_display = os.environ.get("DISPLAY")
    if existing_display:
        try:
            ok, size = _render_test_plot(kernel_path)
            if ok:
                return GraphicsCheckResult(
                    ok=True, mode="display",
                    message=f"图形渲染测试通过 ✓  (使用现有 DISPLAY={existing_display}, PNG {size} bytes)",
                    missing_deps=[],
                )
            # Existing display didn't work — fall through to Xvfb
            logger.info("Existing DISPLAY=%s render failed, trying Xvfb", existing_display)
        except KernelTimeout:
            logger.info("Existing DISPLAY=%s render timed out, trying Xvfb", existing_display)
        except Exception as e:
            logger.info("Existing DISPLAY=%s render error: %s, trying Xvfb", existing_display, e)

    # ------------------------------------------------------------------
    # 2. Check deps and try Xvfb
    # ------------------------------------------------------------------
    missing: list[str] = []

    if not shutil.which("Xvfb"):
        missing.append("xvfb")

    # Shared libs needed by WolframNB (Qt-based renderer)
    _lib_to_pkg = {
        "libfontconfig.so.1": "libfontconfig1",
        "libxkbcommon.so.0": "libxkbcommon0",
        "libEGL.so.1": "libegl1",
    }
    try:
        ldconfig_out = subprocess.run(
            ["ldconfig", "-p"], capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        ldconfig_out = ""  # can't check — will try rendering anyway

    for lib, pkg in _lib_to_pkg.items():
        if ldconfig_out and lib not in ldconfig_out:
            missing.append(pkg)

    if missing:
        return GraphicsCheckResult(
            ok=False,
            mode="none",
            message=(
                f"缺少系统依赖: {', '.join(missing)}\n"
                f"安装命令 (Debian/Ubuntu): sudo apt-get install -y {' '.join(missing)}\n"
                f"图形功能将被禁用。安装后可重新运行 mma-mcp setup 检测。"
            ),
            missing_deps=missing,
        )

    # Start Xvfb
    display = _start_xvfb()
    if not display:
        return GraphicsCheckResult(
            ok=False, mode="none",
            message="Xvfb 已安装但启动失败。",
            missing_deps=[],
        )

    saved_display = os.environ.get("DISPLAY")
    os.environ["DISPLAY"] = display
    try:
        ok, size = _render_test_plot(kernel_path)
        if ok:
            return GraphicsCheckResult(
                ok=True, mode="xvfb",
                message=f"图形渲染测试通过 ✓  (PNG {size} bytes)",
                missing_deps=[],
            )
        return GraphicsCheckResult(
            ok=False, mode="none",
            message=f"渲染输出无效 (size={size})",
            missing_deps=[],
        )
    except KernelTimeout:
        return GraphicsCheckResult(
            ok=False, mode="none",
            message=(
                "渲染超时（30秒）——可能缺少系统依赖。\n"
                f"确认已安装 (Debian/Ubuntu): {_GRAPHICS_INSTALL_HINT}"
            ),
            missing_deps=[],
        )
    except Exception as e:
        return GraphicsCheckResult(
            ok=False, mode="none",
            message=f"渲染测试异常: {e}",
            missing_deps=[],
        )
    finally:
        if saved_display is not None:
            os.environ["DISPLAY"] = saved_display
        else:
            os.environ.pop("DISPLAY", None)
