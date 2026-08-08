"""Application log viewer.

Support on a customer machine usually cannot get a remote desktop session, so the
log has to be readable from the portal. It is served as parsed JSON lines, newest
first, and only the tail — a 10 MB file must never be loaded into memory to answer
a page request.

The records passing through here have already been through the redaction filter on
their way to disk, so no credential can be read back out.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from app.api.deps import AdminSession

router = APIRouter(prefix="/api/logs", tags=["logs"])

#: Bytes read from the end of the file. Enough for a few thousand lines.
TAIL_BYTES = 512 * 1024


class LogEntry(BaseModel):
    timestamp: str | None = None
    level: str | None = None
    logger: str | None = None
    message: str
    extra: dict[str, Any] = {}


class LogPage(BaseModel):
    entries: list[LogEntry]
    file: str
    truncated: bool


@router.get("", response_model=LogPage)
def read_logs(
    request: Request,
    _admin: AdminSession,
    limit: int = Query(default=200, ge=1, le=2000),
    level: str | None = None,
) -> LogPage:
    settings = request.app.state.settings
    path = settings.paths.log_file

    if not path.is_file():
        return LogPage(entries=[], file=str(path), truncated=False)

    size = path.stat().st_size
    truncated = size > TAIL_BYTES
    with path.open("rb") as handle:
        if truncated:
            handle.seek(-TAIL_BYTES, 2)
            handle.readline()  # discard a partial first line
        raw = handle.read().decode("utf-8", errors="replace")

    wanted = level.upper() if level else None
    collected: deque[LogEntry] = deque(maxlen=limit)

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            # A console-formatted or partially written line is still worth showing.
            collected.append(LogEntry(message=line))
            continue
        if wanted and str(payload.get("level", "")).upper() != wanted:
            continue
        collected.append(
            LogEntry(
                timestamp=payload.pop("timestamp", None),
                level=payload.pop("level", None),
                logger=payload.pop("logger", None),
                message=str(payload.pop("message", "")),
                extra={k: v for k, v in payload.items() if not k.startswith("_")},
            )
        )

    return LogPage(entries=list(reversed(collected)), file=str(path), truncated=truncated)
