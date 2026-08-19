"""SSE encoding helpers for the public stream endpoint.

All SSE payloads are JSON-encoded, so user-controlled text (questions,
answers, source paths) can never inject new SSE event lines: JSON escapes
newlines and `ensure_ascii=True` keeps the wire format pure ASCII.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

# Public SSE response headers. X-Accel-Buffering prepares for the Phase 10
# Nginx reverse proxy; Cache-Control prevents intermediate caching of the
# event stream.
SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}

# SSE comment line used as a lightweight keep-alive heartbeat. Comments are
# ignored by SSE clients and never faked as processing stages.
SSE_HEARTBEAT = ": keep-alive\n\n"


def encode_sse_event(event: str, data: BaseModel | dict[str, Any]) -> str:
    """Encode one SSE event with a JSON data payload.

    Guarantees:
    - deterministic JSON serialization (sorted keys, ASCII-escaped);
    - UTF-8/Chinese text survives via JSON unicode escapes;
    - every event ends with a blank line;
    - user text cannot inject additional SSE lines because it is JSON-encoded
      first and JSON output never contains raw newlines.
    """
    payload = data.model_dump() if isinstance(data, BaseModel) else data
    serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return f"event: {event}\ndata: {serialized}\n\n"
