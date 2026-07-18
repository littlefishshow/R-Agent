from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from app_gui.normalizer import safe_json_dumps
from app_gui.schemas import ContextEvent, PayloadRef, new_id


class ContextSnapshotStore:
    """Single-file context bundle for the visual cockpit.

    Each GUI session owns one directory and one context.json file. Events and
    large payload bodies live together in that JSON bundle so deleting a session
    directory removes all of its context at once.
    """

    def __init__(self, base_dir: Union[str, Path] = "outputs/gui_context", preview_chars: int = 800):
        self.base_dir = Path(base_dir)
        self.context_path = self.base_dir / "context.json"
        self.payload_dir = self.base_dir / "payloads"
        self.events_path = self.base_dir / "events.jsonl"
        self.preview_chars = max(1, int(preview_chars))
        self.events: List[Dict[str, Any]] = []
        self.payloads: Dict[str, Dict[str, Any]] = {}
        self.metadata: Dict[str, Any] = {}
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        if self.context_path.exists():
            try:
                data = json.loads(self.context_path.read_text(encoding="utf-8"))
                self.events = list(data.get("events") or [])
                self.payloads = dict(data.get("payloads") or {})
                self.metadata = dict(data.get("metadata") or {})
                return
            except Exception:
                self.events = []
                self.payloads = {}
                self.metadata = {}
        # Backward-compatible one-time read for stores written by older builds.
        if self.events_path.exists():
            try:
                self.events = [
                    json.loads(line)
                    for line in self.events_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            except Exception:
                self.events = []
        if self.payload_dir.exists():
            for path in self.payload_dir.glob("*.txt"):
                payload_id = path.stem
                try:
                    text = path.read_text(encoding="utf-8")
                except Exception:
                    continue
                self.payloads[payload_id] = {"content": text, "content_type": "text/plain"}
        if self.events or self.payloads:
            self._save()

    def _save(self) -> None:
        data = {
            "version": 1,
            "metadata": {
                "base_dir": str(self.base_dir),
                "context_path": str(self.context_path),
                **self.metadata,
            },
            "modules": self._build_modules(),
            # Backward-compatible views used by existing GUI endpoints.
            "events": self.events,
            "payloads": self.payloads,
        }
        tmp_path = self.context_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        tmp_path.replace(self.context_path)

    def update_metadata(self, values: Dict[str, Any]) -> None:
        self.metadata.update(dict(values or {}))
        self._save()

    def _build_modules(self) -> Dict[str, Any]:
        prompts = []
        messages = []
        tool_calls = []
        tool_results = []
        runtime_events = []
        for event in self.events:
            event_type = event.get("event_type")
            payload = event.get("payload") or {}
            if event_type in {"system_prompt_built", "memory_snapshot_loaded"}:
                prompts.append(event)
            elif event_type == "message_appended":
                messages.append({
                    "event_id": event.get("event_id"),
                    "created_at": event.get("created_at"),
                    "message_index": payload.get("message_index"),
                    "message": payload.get("message"),
                    "source": event.get("source"),
                })
            elif event_type == "tool_call_started":
                tool_calls.append(event)
            elif event_type in {"tool_call_finished", "tool_result_appended"}:
                tool_results.append(event)
            else:
                runtime_events.append(event)
        return {
            "prompts": prompts,
            "messages": messages,
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "runtime_events": runtime_events,
            "payloads": self.payloads,
        }

    def put_payload(self, content: Any, *, content_type: str = "text/plain") -> PayloadRef:
        if isinstance(content, str):
            text = content
        else:
            text = safe_json_dumps(content)
        payload_id = new_id("payload")
        self.payloads[payload_id] = {"content": text, "content_type": content_type}
        self._save()
        return PayloadRef(
            id=payload_id,
            preview=text[: self.preview_chars],
            size=len(text),
            truncated=len(text) > self.preview_chars,
            content_type=content_type,
        )

    def get_payload(self, payload_id: str) -> str:
        if payload_id in self.payloads:
            return str(self.payloads[payload_id].get("content") or "")
        # Last-resort compatibility for stores not yet migrated.
        path = self.payload_dir / f"{payload_id}.txt"
        return path.read_text(encoding="utf-8")

    def append_event(self, event: ContextEvent | Dict[str, Any]) -> Dict[str, Any]:
        data = event.to_dict() if isinstance(event, ContextEvent) else dict(event)
        self.events.append(data)
        self._save()
        return data

    def replace_message_events(self, messages: List[Dict[str, Any]], *, session_id: str = "") -> None:
        """Replace persisted conversation messages while preserving prompt/runtime setup events."""
        self.events = [event for event in self.events if event.get("event_type") != "message_appended"]
        now = time.time()
        for index, message in enumerate(messages):
            self.events.append({
                "event_id": new_id("event"),
                "event_type": "message_appended",
                "session_id": session_id,
                "source": "context_rewrite",
                "created_at": now + index * 0.0001,
                "payload": {
                    "message": message,
                    "message_index": index,
                },
            })
        self._save()

    def list_events(self, *, event_type: Optional[str] = None) -> List[Dict[str, Any]]:
        if event_type is None:
            return list(self.events)
        return [event for event in self.events if event.get("event_type") == event_type]
