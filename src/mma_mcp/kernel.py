"""Wolfram kernel lifecycle management.

Maintains a persistent WolframLanguageSession. On evaluation failure due to
kernel crash the session is automatically restarted and the call retried once.
"""

from __future__ import annotations

import base64
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
    if not lock.exists():
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

    def evaluate(self, expr: Any, *, retry: bool = True) -> Any:
        """Evaluate a WL expression. Returns the Python-converted result.

        Accepts anything wolframclient accepts: wl.* objects, wlexpr strings,
        or raw WL expression objects.
        """
        self._ensure_started()
        try:
            return self._session.evaluate(expr)
        except WolframKernelException:
            if retry:
                logger.warning("Kernel exception — restarting and retrying once")
                self.restart()
                return self._session.evaluate(expr)
            raise

    def evaluate_to_string(
        self, expr_str: str, form: str = "TeXForm", timeout: int = 0,
    ) -> str:
        """Evaluate a WL expression string and return the result as a string.

        Args:
            expr_str: Wolfram Language expression.
            form:     Output format (TeXForm, OutputForm, InputForm, …).
            timeout:  Seconds. 0 means no timeout.
        """
        if timeout > 0:
            inner = f"TimeConstrained[{expr_str}, {timeout}]"
        else:
            inner = expr_str
        wrapped = wl.ToString(wlexpr(inner), wlexpr(form))
        result = self.evaluate(wrapped)
        if isinstance(result, str):
            return result
        return str(result)

    def evaluate_to_image(self, expr_str: str, timeout: int = 0) -> bytes:
        """Evaluate a WL expression and export the result as PNG bytes.

        Wraps the expression in Rasterize so that any Graphics/Plot output
        is captured even if the expression is not inherently graphical.

        Args:
            expr_str: Wolfram Language expression.
            timeout:  Seconds. 0 means no timeout.
        """
        self._ensure_started()
        if timeout > 0:
            inner = f"TimeConstrained[{expr_str}, {timeout}]"
        else:
            inner = expr_str

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name

        export_expr = wl.Export(
            tmp_path,
            wl.Rasterize(wlexpr(inner), wlexpr('ImageResolution -> 144')),
            "PNG",
        )
        self.evaluate(export_expr)
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
