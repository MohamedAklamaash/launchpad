"""Server-Sent Events framing shared by every log-stream view (runtime logs, build logs).

An SSE stream is plain text over a normal HTTP response: each frame is one or more
`field: value` lines terminated by a blank line. `event()` builds a `data:`/`event:`/`id:`
frame from a JSON-serializable payload; `heartbeat()` builds a comment line (starts with
`:`, which every SSE client ignores as a value but still treats as proof the connection is
alive) sent on an interval so an idle connection isn't mistaken for a dead one by any proxy
in between.

`data:` carries exactly one line: `json.dumps` never emits a literal newline, so the
payload can't accidentally start a second `data:` line or get truncated by one.
"""
import json

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-store",
    "X-Accel-Buffering": "no",
}
SSE_CONTENT_TYPE = "text/event-stream"

HEARTBEAT_INTERVAL_SECONDS = 15


def event(data: dict, *, event_type: str | None = None, frame_id: str | None = None) -> bytes:
    lines = []
    if frame_id is not None:
        lines.append(f"id: {frame_id}")
    if event_type is not None:
        lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(data, separators=(',', ':'))}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def heartbeat() -> bytes:
    return b": ping\n\n"
