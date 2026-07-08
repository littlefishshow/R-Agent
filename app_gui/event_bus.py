from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from app_gui.schemas import ContextEvent
from app_gui.snapshot_store import ContextSnapshotStore


def _fold_large_strings(value: Any, store: ContextSnapshotStore, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return {"payload_ref": store.put_payload(value).to_dict()}
    if isinstance(value, dict):
        return {k: _fold_large_strings(v, store, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_fold_large_strings(v, store, limit) for v in value]
    return value


class ContextEventBus:
    """Small synchronous event bus used by Agent runtime and future GUI server."""

    def __init__(self, store: Optional[ContextSnapshotStore] = None, session_id: str = "default", fold_large_strings: bool = True):
        self.store = store
        self.session_id = session_id
        self.fold_large_strings = fold_large_strings
        self._subscribers: List[Callable[[Dict[str, Any]], None]] = []
        self.events: List[Dict[str, Any]] = []

    def subscribe(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        self._subscribers.append(callback)

    def emit(self, event_type: str, payload: Optional[Dict[str, Any]] = None, *, source: str = "main_agent") -> Dict[str, Any]:
        event = ContextEvent(
            event_type=event_type,
            session_id=self.session_id,
            payload=(
                _fold_large_strings(payload or {}, self.store, self.store.preview_chars)
                if self.store is not None and self.fold_large_strings else (payload or {})
            ),
            source=source,
        )
        data = self.store.append_event(event) if self.store is not None else event.to_dict()
        self.events.append(data)
        for subscriber in list(self._subscribers):
            subscriber(data)
        return data

    def __call__(self, event_type: str, payload: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
        source = kwargs.pop("source", "main_agent")
        merged = dict(payload or {})
        merged.update(kwargs)
        return self.emit(event_type, merged, source=source)
