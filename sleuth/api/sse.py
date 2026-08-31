def sse_frame(event: str | None, data: str) -> str:
    """Format one Server-Sent Events frame.

    A "data:" field can't carry an embedded newline on a single line — the
    SSE spec terminates a field at the first "\n", so a multi-line payload
    (a streamed token that lands on a code-block boundary, a JSON blob)
    would silently truncate if written as one "data: ...\n\n" line. Each
    physical line of `data` gets its own "data:"-prefixed line instead; per
    spec, consecutive "data:" lines within one frame are rejoined with "\n"
    by a spec-compliant reader to reconstruct the original payload.

    Shared by chat.py's answer stream and repos.py's progress stream — both
    speak the same framing, just different event names/payloads.
    """
    prefix = f"event: {event}\n" if event else ""
    lines = "".join(f"data: {line}\n" for line in data.split("\n"))
    return f"{prefix}{lines}\n"


def sse_heartbeat() -> str:
    # A bare ":"-prefixed line is an SSE comment: spec-legal, ignored by any
    # conformant reader (EventSource or a hand-rolled line-buffered parser
    # like this project's, as long as it skips lines it doesn't recognize),
    # but it's still bytes on the wire — enough to stop a reverse proxy or
    # browser from treating a long quiet stretch (e.g. a slow embedding
    # batch between progress steps) as a dead connection and closing it.
    return ": keepalive\n\n"
