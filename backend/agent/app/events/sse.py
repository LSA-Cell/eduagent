"""SSE 帧编码：event/id/data 三段式，id 即 seq（供 Last-Event-ID 重放）。"""

from __future__ import annotations

import json

from app.events.protocol import SSEEvent


def sse_encode(event: SSEEvent) -> str:
    """编码为 text/event-stream 帧：``id: seq`` + ``event:`` + ``data:``。"""
    payload = json.dumps(event.data, ensure_ascii=False, default=str)
    return f"id: {event.seq}\nevent: {event.event}\ndata: {payload}\n\n"
