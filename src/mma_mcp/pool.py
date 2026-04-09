"""Wolfram kernel worker pool for isolated evaluation.

Each tool call acquires an exclusive worker from the pool, evaluates in a
temporary WL context, cleans up, and returns the worker.  This provides
process-level isolation between concurrent clients.

Pool behaviour (inspired by Apache prefork MPM):
  - Lazy creation: only ``pool_min_idle`` workers at startup, rest on demand.
  - Idle reclaim: excess workers stopped after ``idle_timeout`` seconds.
  - Periodic restart: worker recycled after ``max_requests_per_worker`` calls
    to prevent memory bloat from long-running kernels.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Generator

from wolframclient.language import wlexpr

from mma_mcp.kernel import KernelSession

logger = logging.getLogger(__name__)


@dataclass
class _Worker:
    """A single kernel worker in the pool."""
    session: KernelSession
    request_count: int = 0
    last_used: float = field(default_factory=time.monotonic)


class KernelPool:
    """Pool of Wolfram kernel workers.

    Workers are created lazily up to ``pool_size``.  ``worker()`` grants
    exclusive access to one worker via a context manager; on exit the
    temporary WL context is cleaned up and the worker is returned.
    """

    def __init__(
        self,
        kernel_path: str | None = None,
        pool_size: int = 0,
        pool_min_idle: int = 1,
        max_requests_per_worker: int = 100,
        idle_timeout: int = 0,
    ) -> None:
        self._kernel_path = kernel_path
        self._pool_size = pool_size if pool_size > 0 else min(os.cpu_count() or 2, 4)
        self._min_idle = max(1, min(pool_min_idle, self._pool_size))
        self._max_requests = max_requests_per_worker
        self._idle_timeout = idle_timeout

        self._lock = threading.Lock()
        self._semaphore = threading.Semaphore(self._pool_size)
        self._all_workers: list[_Worker] = []
        self._idle: list[_Worker] = []

        # Pre-create min_idle workers (not started — lazy start on first use)
        for _ in range(self._min_idle):
            w = self._create_worker()
            self._all_workers.append(w)
            self._idle.append(w)

        # Start reaper thread if idle reclaim is possible
        self._reaper_stop = threading.Event()
        self._reaper_thread: threading.Thread | None = None
        if self._idle_timeout > 0 and self._pool_size > self._min_idle:
            self._reaper_thread = threading.Thread(
                target=self._reaper_loop, daemon=True, name="pool-reaper",
            )
            self._reaper_thread.start()

        logger.info(
            "Kernel pool ready (size=%d, min_idle=%d, max_req=%d)",
            self._pool_size, self._min_idle, self._max_requests,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @contextmanager
    def worker(self) -> Generator[tuple[KernelSession, str], None, None]:
        """Acquire an exclusive worker and temporary WL context.

        Usage::

            with pool.worker() as (kernel, wl_context):
                result = kernel.evaluate_to_string(expr, form, context=wl_context)

        On exit the temp context symbols are removed and the worker is
        returned to the pool.
        """
        w = self._acquire()
        ctx_name = f"Pool${secrets.token_hex(4)}`"
        try:
            yield w.session, ctx_name
        finally:
            self._cleanup(w, ctx_name)
            w.request_count += 1
            w.last_used = time.monotonic()
            self._release(w)

    def stop(self) -> None:
        """Shut down all workers and the reaper thread."""
        if self._reaper_thread is not None:
            self._reaper_stop.set()
            self._reaper_thread.join(timeout=5)
        with self._lock:
            workers = list(self._all_workers)
            self._all_workers.clear()
            self._idle.clear()
        for w in workers:
            try:
                w.session.stop()
            except Exception:
                pass
        logger.info("Kernel pool stopped")

    @property
    def size(self) -> int:
        """Total number of workers (idle + busy)."""
        with self._lock:
            return len(self._all_workers)

    @property
    def idle_count(self) -> int:
        """Number of idle workers available for immediate use."""
        with self._lock:
            return len(self._idle)

    # ------------------------------------------------------------------
    # Internal: acquire / release
    # ------------------------------------------------------------------

    def _create_worker(self) -> _Worker:
        return _Worker(session=KernelSession(kernel=self._kernel_path))

    def _acquire(self) -> _Worker:
        """Get an idle worker, or create a new one (blocks if at capacity)."""
        self._semaphore.acquire()
        with self._lock:
            if self._idle:
                w = self._idle.pop()
            else:
                w = self._create_worker()
                self._all_workers.append(w)
                logger.info("Pool: created worker (total=%d)", len(self._all_workers))
        # start() is idempotent and thread-safe (has its own lock)
        w.session.start()
        return w

    def _release(self, worker: _Worker) -> None:
        """Return a worker to the pool, or recycle it if max requests reached."""
        recycle = (
            self._max_requests > 0
            and worker.request_count >= self._max_requests
        )
        with self._lock:
            if recycle:
                logger.info("Pool: recycling worker after %d requests", worker.request_count)
                if worker in self._all_workers:
                    self._all_workers.remove(worker)
                # Create replacement
                new_w = self._create_worker()
                self._all_workers.append(new_w)
                self._idle.append(new_w)
            else:
                self._idle.append(worker)
        # Stop old worker outside lock (slow)
        if recycle:
            try:
                worker.session.stop()
            except Exception:
                pass
        self._semaphore.release()

    # ------------------------------------------------------------------
    # Internal: cleanup
    # ------------------------------------------------------------------

    @staticmethod
    def _cleanup(worker: _Worker, ctx_name: str) -> None:
        """Remove all symbols created in the temporary context."""
        try:
            worker.session.evaluate(wlexpr(f'Quiet[Remove["{ctx_name}*"]]'))
        except Exception:
            logger.debug("Context cleanup failed for %s", ctx_name, exc_info=True)

    # ------------------------------------------------------------------
    # Internal: idle reaper
    # ------------------------------------------------------------------

    def _reaper_loop(self) -> None:
        """Periodically reclaim excess idle workers."""
        while not self._reaper_stop.wait(timeout=30):
            self._reap_idle()

    def _reap_idle(self) -> None:
        """Stop idle workers that exceed ``pool_min_idle`` and have timed out."""
        now = time.monotonic()
        to_stop: list[_Worker] = []
        with self._lock:
            while len(self._idle) > self._min_idle:
                w = self._idle[0]  # oldest idle
                if now - w.last_used >= self._idle_timeout:
                    self._idle.pop(0)
                    self._all_workers.remove(w)
                    to_stop.append(w)
                else:
                    break
        for w in to_stop:
            logger.info("Pool: reaping idle worker")
            try:
                w.session.stop()
            except Exception:
                pass
