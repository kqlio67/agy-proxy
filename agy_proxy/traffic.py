"""
Live Traffic Inspector for Antigravity Proxy.
Maintains a thread-safe in-memory ring buffer of recent inference transactions
and broadcasts live event streams over Server-Sent Events (SSE).
"""

from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime
import asyncio
import json
import time
from typing import Any, AsyncGenerator, Optional
import uuid


@dataclass
class TrafficRecord:
    id: str
    timestamp: str  # HH:MM:SS
    method: str     # "POST", "GET", etc.
    path: str       # "/v1/messages", "/v1/chat/completions", etc.
    client_model: str
    backend_model: str
    account_id: str
    account_email: str
    status_code: int
    duration_ms: float
    stream: bool
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    thoughts_tokens: int = 0
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TrafficInspector:
    """Thread-safe ring buffer and SSE event broadcaster for proxy traffic."""

    def __init__(self, maxlen: int = 200):
        self._history: deque[TrafficRecord] = deque(maxlen=maxlen)
        self._subscribers: set[asyncio.Queue] = set()

    def record(
        self,
        method: str,
        path: str,
        client_model: str,
        backend_model: str = "",
        account_id: str = "",
        account_email: str = "",
        status_code: int = 200,
        duration_ms: float = 0.0,
        stream: bool = False,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        thoughts_tokens: int = 0,
        error: Optional[str] = None,
        record_id: Optional[str] = None,
    ) -> TrafficRecord:
        """Records an HTTP transaction and broadcasts to active SSE listeners."""
        rec_id = record_id or f"tr_{uuid.uuid4().hex[:8]}"
        now_str = datetime.now().strftime("%H:%M:%S")

        if total_tokens == 0 and (prompt_tokens or completion_tokens or thoughts_tokens):
            total_tokens = prompt_tokens + completion_tokens

        rec = TrafficRecord(
            id=rec_id,
            timestamp=now_str,
            method=method.upper(),
            path=path,
            client_model=client_model or "unknown",
            backend_model=backend_model or client_model or "unknown",
            account_id=account_id or "default",
            account_email=account_email or account_id or "default",
            status_code=status_code,
            duration_ms=round(duration_ms, 1),
            stream=stream,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            thoughts_tokens=thoughts_tokens,
            error=error,
        )

        self._history.append(rec)
        self._broadcast(rec)
        return rec

    def _broadcast(self, record: TrafficRecord) -> None:
        """Distributes new record to all open SSE client queues."""
        if not self._subscribers:
            return
        payload = json.dumps(record.to_dict())
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except Exception:
                # If queue full or subscriber died, drop gracefully
                pass

    def get_history(self, limit: int = 100) -> list[dict[str, Any]]:
        """Returns the most recent records, ordered newest first."""
        records = list(self._history)
        if limit and limit < len(records):
            records = records[-limit:]
        return [r.to_dict() for r in reversed(records)]

    def clear(self) -> None:
        """Clears all traffic records from the ring buffer."""
        self._history.clear()

    async def subscribe(self) -> AsyncGenerator[str, None]:
        """Async generator yielding SSE formatted events for new traffic records."""
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        try:
            # Send initial keep-alive ping
            yield ": connected\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            self._subscribers.discard(q)


traffic_inspector = TrafficInspector(maxlen=200)
