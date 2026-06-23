import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple


@dataclass
class EventDeduplicator:
    """TTL-based in-memory event id deduplicator for webhook retries."""

    ttl_seconds: int = 3600
    max_items: int = 10000
    _seen: Dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def seen_or_mark(self, event_id: str) -> bool:
        if not event_id:
            return False
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            if event_id in self._seen:
                return True
            if len(self._seen) >= self.max_items:
                oldest = min(self._seen, key=self._seen.get)
                self._seen.pop(oldest, None)
            self._seen[event_id] = now
            return False

    def _purge_locked(self, now: float) -> None:
        expired = [key for key, ts in self._seen.items() if now - ts > self.ttl_seconds]
        for key in expired:
            self._seen.pop(key, None)


@dataclass
class AsyncJobQueue:
    """Tiny background worker queue for webhook tasks.

    This is intentionally small and dependency-free. Production deployments can
    replace it with Redis/RQ/Celery without changing adapter boundaries.
    """

    maxsize: int = 100
    workers: int = 1
    _queue: "queue.Queue[Tuple[Callable, tuple, dict]]" = field(init=False)
    _started: bool = field(default=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        self._queue = queue.Queue(maxsize=self.maxsize)

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            for idx in range(max(1, self.workers)):
                thread = threading.Thread(target=self._worker, name=f"ragent-gateway-worker-{idx}", daemon=True)
                thread.start()

    def submit(self, fn: Callable, *args, **kwargs) -> bool:
        self.start()
        try:
            self._queue.put_nowait((fn, args, kwargs))
            return True
        except queue.Full:
            return False

    def _worker(self) -> None:
        while True:
            fn, args, kwargs = self._queue.get()
            try:
                fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - background boundary
                print(f"[gateway-worker] job failed: {exc}")
            finally:
                self._queue.task_done()
