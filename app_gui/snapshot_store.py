from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from app_gui.normalizer import safe_json_dumps
from app_gui.schemas import ContextEvent, PayloadRef, new_id


class ContextSnapshotStore:
    """Append-only event store plus lazy-load payload storage for the visual cockpit."""

    def __init__(self, base_dir: Union[str, Path] = "outputs/gui_context", preview_chars: int = 800):
        self.base_dir = Path(base_dir)
        self.payload_dir = self.base_dir / "payloads"
        self.events_path = self.base_dir / "events.jsonl"
        self.preview_chars = max(1, int(preview_chars))
        self.events: List[Dict[str, Any]] = []
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.payload_dir.mkdir(parents=True, exist_ok=True)

    def put_payload(self, content: Any, *, content_type: str = "text/plain") -> PayloadRef:
        if isinstance(content, str):
            text = content
        else:
            text = safe_json_dumps(content)
        payload_id = new_id("payload")
        payload_path = self.payload_dir / f"{payload_id}.txt"
        payload_path.write_text(text, encoding="utf-8")
        return PayloadRef(
            id=payload_id,
            preview=text[: self.preview_chars],
            size=len(text),
            truncated=len(text) > self.preview_chars,
            content_type=content_type,
        )

    def get_payload(self, payload_id: str) -> str:
        path = self.payload_dir / f"{payload_id}.txt"
        return path.read_text(encoding="utf-8")

    def append_event(self, event: ContextEvent | Dict[str, Any]) -> Dict[str, Any]:
        data = event.to_dict() if isinstance(event, ContextEvent) else dict(event)
        self.events.append(data)
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
        return data

    def list_events(self, *, event_type: Optional[str] = None) -> List[Dict[str, Any]]:
        if event_type is None:
            return list(self.events)
        return [event for event in self.events if event.get("event_type") == event_type]
