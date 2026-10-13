"""Server-Sent Events: the broadcaster and GET /events.

The sidebar keeps one /events stream open; background work (the idle loop and the
single worker thread) pushes events to it with ``broadcast_from_thread``.

``_sse_clients`` and ``_event_loop`` live here and nowhere else. Other modules reach
them (and the functions) as ``events.<name>`` at call time, never with
``from app.api.events import ...``, so a test that patches this module is seen by every
caller (plan rule M1).
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()

# ---------------------------------------------------------------------------
# SSE event broadcasting
# ---------------------------------------------------------------------------
_sse_clients: list[asyncio.Queue] = []
_event_loop: asyncio.AbstractEventLoop | None = None


async def _broadcast(event: str, data: dict) -> None:
    """Push an SSE event to all connected sidebar clients."""
    if not _sse_clients:
        return
    msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    for q in list(_sse_clients):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass


def broadcast_from_thread(event: str, data: dict) -> None:
    """Thread-safe wrapper — call from sync background tasks."""
    if _event_loop is None:
        return
    asyncio.run_coroutine_threadsafe(_broadcast(event, data), _event_loop)


@router.get("/events")
async def sse_events(request: Request):
    """Server-Sent Events stream. Pushes job_processed and cover_letter_ready events."""
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=50)
    _sse_clients.append(queue)

    async def stream():
        try:
            yield "event: ping\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=25)
                    yield msg
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"  # keepalive
        finally:
            if queue in _sse_clients:
                _sse_clients.remove(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
