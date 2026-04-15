# TODO: Async Evaluation with Progress Heartbeat

## Problem

When a Wolfram kernel evaluation takes longer than the client's timeout
(Claude.ai ~30s, ChatGPT unknown), the client disconnects. This triggers
an unhandled `ClientDisconnect` in the MCP SDK, crashing the server process.
systemd restarts it, but the client takes additional time to reconnect.
For users who regularly run non-trivial computations (DSolve, NIntegrate,
large symbolic manipulations), this makes the service unreliable.

## Goal

Send periodic progress notifications during kernel evaluation so that
the client knows the server is alive and doesn't disconnect prematurely.

## Current Architecture

```
Client  -->  FastMCP (_safe_wrapper)  -->  evaluate(ctx, expr)
                                              |
                                              v
                                         ctx.pool.worker()  -->  kernel.evaluate_to_string()
                                                                      |
                                                                      v
                                                                 session.evaluate()  [blocking, in ThreadPoolExecutor]
```

- Tool functions are **synchronous** (`def evaluate(ctx, ...)`)
- `_safe_wrapper` in `tools/__init__.py` wraps them for RBAC + error handling
- Kernel evaluation blocks in `ThreadPoolExecutor` with `hard_timeout`
- FastMCP supports **async** tool functions with `Context.report_progress()`

## SDK Support

FastMCP provides `Context` (type hint injection):

```python
from mcp.server.fastmcp import Context

@server.tool()
async def my_tool(x: int, ctx: Context) -> str:
    await ctx.report_progress(0, 100, "Starting...")
    # ... do work ...
    await ctx.report_progress(100, 100, "Done")
    return result
```

`report_progress()` sends MCP `notifications/progress` over the SSE stream,
which should reset the client's idle timeout. The client must provide a
`progressToken` in the request `_meta` for this to work.

## Implementation Plan

### Phase 1: Async tool functions with heartbeat ✔ (implemented)

**Files:** `tools/evaluate.py`, `tools/__init__.py`, `kernel.py`

1. **Make tool functions async.** Change `def evaluate(ctx, ...)` to
   `async def evaluate(ctx, ...)`. FastMCP supports both sync and async tools.

2. **Accept FastMCP Context in tool functions.** Add a `Context` parameter
   to receive the MCP request context. Our `_safe_wrapper` must be updated
   to pass it through (currently it strips `ctx` from the signature; now it
   needs to also handle `Context`).

3. **Run kernel evaluation in a background thread with heartbeat loop.**
   Instead of blocking on `kernel.evaluate_to_string()`, submit the
   evaluation to a thread and poll with heartbeat.

   **Critical:** `pool.worker()` must be acquired inside the thread
   function, not in the async layer. Otherwise, if the client disconnects
   and the async task is cancelled, `CancelledError` triggers the context
   manager's `__exit__` and releases the worker — while the kernel thread
   is still running. This leads to concurrent reuse of the same kernel.

   ```python
   async def _run_with_heartbeat(func, mcp_ctx, hard_timeout):
       loop = asyncio.get_running_loop()
       future = loop.run_in_executor(None, func)
       elapsed = 0
       while not future.done():
           try:
               return await asyncio.wait_for(
                   asyncio.shield(future), timeout=5,
               )
           except asyncio.TimeoutError:
               elapsed += 5
               if mcp_ctx:
                   await mcp_ctx.report_progress(
                       elapsed, hard_timeout or None,
                       f"Computing… ({elapsed}s)",
                   )
       return future.result()

   async def evaluate(ctx, expression, form="", mcp_ctx=None):
       ctx.check(expression)
       fmt = form or ctx.default_format

       def _do_eval():
           # Worker lifecycle is bound to the thread — safe from
           # async cancellation.
           with ctx.pool.worker() as (kernel, wl_context):
               return kernel.evaluate_to_string(
                   expression, fmt,
                   timeout=ctx.timeout, hard_timeout=ctx.hard_timeout,
                   context=wl_context,
               )

       result = await _run_with_heartbeat(_do_eval, mcp_ctx, ctx.hard_timeout)
       return ctx.truncate(result)
   ```

4. **Update `_safe_wrapper`** to handle async functions (use `await` if
   the wrapped function is a coroutine) and pass through the `Context`
   parameter from FastMCP. The wrapper renames `mcp_ctx` → `ctx` in the
   exposed signature so FastMCP auto-injects its `Context` object.

### Phase 2: Verify client behavior

- Test whether Claude.ai sends `progressToken` in `_meta` and whether
  receiving `notifications/progress` actually prevents disconnection.
- Test ChatGPT's behavior with progress notifications.
- If clients don't send `progressToken`, `report_progress()` is a no-op
  (see SDK source: returns early if token is None). In that case, the
  heartbeat has no effect and we need to investigate SSE-level keep-alive
  or other approaches.

### Phase 3: Graceful client disconnection handling

Even with heartbeat, clients may still disconnect (network issues, user
navigating away). Currently this crashes the server via unhandled
`ClientDisconnect` in the MCP SDK.

**Partial mitigation (Phase 1):** Worker lifecycle is bound to the
executor thread, not the async task. When a client disconnects and the
task is cancelled, the kernel thread runs to completion and releases the
worker normally — no orphaned kernel or concurrent reuse.

- Monitor MCP SDK for fixes to `ClientDisconnect` handling in
  `streamable_http.py`.
- If unfixed, consider wrapping the server entrypoint to catch and log
  `ClientDisconnect` instead of crashing. This may require patching or
  subclassing the SDK's HTTP handler.

## Risk Assessment

- **Phase 1** is self-contained and backwards-compatible. Sync tools still
  work; async is additive. The heartbeat is a no-op if the client doesn't
  support `progressToken`.
- **Phase 2** depends on client behavior we can't control. If clients
  ignore progress notifications, we may need SSE-level keep-alive instead.
- **Phase 3** depends on MCP SDK evolution.

## Files to Modify

| File | Change |
|------|--------|
| `tools/evaluate.py` | sync → async, add heartbeat loop |
| `tools/__init__.py` | `_safe_wrapper` async support, Context passthrough |
| `kernel.py` | No change needed (stays sync, called from executor) |
| `pool.py` | No change needed (context manager is sync, usable in executor) |
