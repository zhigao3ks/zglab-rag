"""Safe production observability helpers for the public HTTP boundary."""

from __future__ import annotations

import json
import logging


def log_http_request(
    logger: logging.Logger,
    *,
    request_id: str,
    path: str,
    latency_ms: float,
    status: int,
    error_code: str | None,
) -> None:
    """Emit one JSON access record without request bodies or internal paths."""
    logger.info(
        "%s",
        json.dumps(
            {
                "event": "http_request",
                "request_id": request_id,
                "path": path,
                "latency_ms": round(latency_ms, 3),
                "status": status,
                "error_code": error_code,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
