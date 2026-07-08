from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class PayloadRef:
    id: str
    preview: str
    size: int
    truncated: bool
    content_type: str = "text/plain"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "preview": self.preview,
            "size": self.size,
            "truncated": self.truncated,
            "content_type": self.content_type,
        }


@dataclass
class ContextEvent:
    event_type: str
    session_id: str = "default"
    payload: Dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: new_id("evt"))
    created_at: float = field(default_factory=time.time)
    source: str = "main_agent"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "session_id": self.session_id,
            "source": self.source,
            "created_at": self.created_at,
            "payload": self.payload,
        }


@dataclass
class MessageRecord:
    role: str
    content: str = ""
    message_id: str = field(default_factory=lambda: new_id("msg"))
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    source: str = "main_agent"
    raw_type: str = "dict"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "role": self.role,
            "content": self.content,
            "name": self.name,
            "tool_call_id": self.tool_call_id,
            "tool_calls": self.tool_calls,
            "source": self.source,
            "raw_type": self.raw_type,
        }


@dataclass
class ToolCallRecord:
    name: str
    arguments: str
    call_id: Optional[str] = None
    result: Optional[str] = None
    isolated: bool = True
    result_ref: Optional[PayloadRef] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "call_id": self.call_id,
            "name": self.name,
            "arguments": self.arguments,
            "isolated": self.isolated,
            "result": self.result,
        }
        if self.result_ref is not None:
            data["result_ref"] = self.result_ref.to_dict()
        return data


@dataclass
class LLMRequestSnapshot:
    model: str
    messages: List[Dict[str, Any]]
    tools: List[Dict[str, Any]] = field(default_factory=list)
    request_id: str = field(default_factory=lambda: new_id("llmreq"))
    iteration: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "iteration": self.iteration,
            "model": self.model,
            "messages": self.messages,
            "tools": self.tools,
            "message_count": len(self.messages),
            "tool_schema_count": len(self.tools),
        }


EVENT_SESSION_STARTED = "session_started"
EVENT_SYSTEM_PROMPT_BUILT = "system_prompt_built"
EVENT_MEMORY_SNAPSHOT_LOADED = "memory_snapshot_loaded"
EVENT_USER_INPUT_RECEIVED = "user_input_received"
EVENT_LLM_REQUEST_SNAPSHOT = "llm_request_snapshot"
EVENT_LLM_RESPONSE_RECEIVED = "llm_response_received"
EVENT_MESSAGE_APPENDED = "message_appended"
EVENT_TOOL_CALL_STARTED = "tool_call_started"
EVENT_TOOL_CALL_FINISHED = "tool_call_finished"
EVENT_TOOL_RESULT_APPENDED = "tool_result_appended"
EVENT_ARCHIVE_COMPRESSED = "archive_compressed"
EVENT_TRUNCATION_FORCED = "truncation_forced"
EVENT_SELF_REVIEW_STARTED = "self_review_started"
EVENT_SELF_REVIEW_FINISHED = "self_review_finished"
EVENT_DELEGATE_SUBAGENT_STARTED = "delegate_subagent_started"
EVENT_DELEGATE_SUBAGENT_FINISHED = "delegate_subagent_finished"
EVENT_ERROR = "error"
