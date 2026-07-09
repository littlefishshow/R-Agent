from __future__ import annotations

import queue
import threading
from typing import Callable, TypeVar


T = TypeVar("T")


class AutoResearchTimeoutError(TimeoutError):
    pass


def call_with_deadline(fn: Callable[[], T], *, timeout_seconds: float, label: str) -> T:
    """Run fn with a framework-side wall-clock deadline.

    Some LLM SDK/provider combinations treat request timeout as best-effort. This
    helper lets the phase machine move on when the provider call has not returned
    within the configured budget. The worker thread is daemonized so a late SDK
    return cannot keep the autoresearch process alive.
    """
    timeout = float(timeout_seconds or 0.0)
    if timeout <= 0:
        return fn()
    result_queue: "queue.Queue[tuple[str, object]]" = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            result_queue.put(("ok", fn()), block=False)
        except Exception as exc:
            try:
                result_queue.put(("err", exc), block=False)
            except Exception:
                pass

    thread = threading.Thread(target=worker, name=f"autoresearch-deadline-{label[:32]}", daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        raise AutoResearchTimeoutError(f"{label} exceeded framework deadline of {timeout:.1f}s")
    try:
        status, payload = result_queue.get_nowait()
    except queue.Empty as exc:
        raise AutoResearchTimeoutError(f"{label} finished without returning a result") from exc
    if status == "err":
        raise payload  # type: ignore[misc]
    return payload  # type: ignore[return-value]


__all__ = ["AutoResearchTimeoutError", "call_with_deadline"]
