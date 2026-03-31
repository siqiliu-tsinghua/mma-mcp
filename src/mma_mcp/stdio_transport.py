"""Custom stdio transport for MCP.

Bypasses the SDK's stdio helper which can hang in pipe-based environments
(e.g. VSCode extensions). Uses asyncio.connect_read_pipe for stdin and
writes directly to sys.stdout.buffer for stdout.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

import anyio
import anyio.lowlevel
import mcp.types as types
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.shared.message import SessionMessage


@asynccontextmanager
async def stdio_transport() -> AsyncIterator[
    tuple[
        MemoryObjectReceiveStream[SessionMessage | Exception],
        MemoryObjectSendStream[SessionMessage],
    ]
]:
    """Stdio transport using asyncio pipe reader + direct stdout.buffer writes."""

    loop = asyncio.get_running_loop()

    # --- stdin: use asyncio StreamReader via connect_read_pipe ---
    reader = asyncio.StreamReader()
    read_protocol = asyncio.StreamReaderProtocol(reader)
    # sys.stdin.buffer is the raw binary buffer; works for both pipes and TTYs
    await loop.connect_read_pipe(lambda: read_protocol, sys.stdin.buffer)

    # --- stdout: write directly to sys.stdout.buffer (avoids connect_write_pipe) ---
    stdout_buf = sys.stdout.buffer

    read_stream_writer, read_stream = anyio.create_memory_object_stream[
        SessionMessage | Exception
    ](0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream[SessionMessage](0)

    async def stdin_reader() -> None:
        async with read_stream_writer:
            while True:
                try:
                    line = await reader.readline()
                except Exception as exc:
                    await read_stream_writer.send(exc)
                    break
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    message = types.JSONRPCMessage.model_validate_json(line)
                    await read_stream_writer.send(SessionMessage(message))
                except Exception as exc:
                    await read_stream_writer.send(exc)

    async def stdout_writer() -> None:
        async with write_stream_reader:
            async for session_message in write_stream_reader:
                payload = session_message.message.model_dump_json(
                    by_alias=True, exclude_none=True
                )
                stdout_buf.write((payload + "\n").encode("utf-8"))
                stdout_buf.flush()
                await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as tg:
        tg.start_soon(stdin_reader)
        tg.start_soon(stdout_writer)
        yield read_stream, write_stream
