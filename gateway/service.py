import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.agent import RAgent
from core.prompt_builder import build_system_prompt


@dataclass
class ChatTurn:
    role: str
    content: str
    created_at: float = field(default_factory=time.time)


@dataclass
class AgentSession:
    session_id: str
    agent: RAgent = field(default_factory=RAgent)
    lock: threading.Lock = field(default_factory=threading.Lock)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    turns: List[ChatTurn] = field(default_factory=list)


class AgentSessionManager:
    """Small in-process session manager for HTTP/webhook deployments.

    The CLI keeps one long-lived RAgent instance. The gateway needs multiple
    independent chat histories, so this manager maps external conversation IDs
    (HTTP session_id, WeChat FromUserName, Feishu chat_id/open_id) to isolated
    RAgent instances. Each session has a lock because RAgent.messages is mutable.
    """

    def __init__(self, max_sessions: int = 100, system_prompt: Optional[str] = None):
        self.max_sessions = max_sessions
        self.system_prompt = system_prompt or build_system_prompt()
        self._sessions: Dict[str, AgentSession] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str) -> AgentSession:
        session_id = session_id or "default"
        with self._lock:
            if session_id in self._sessions:
                return self._sessions[session_id]
            self._evict_if_needed_locked()
            session = AgentSession(session_id=session_id)
            self._sessions[session_id] = session
            return session

    def reset(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def list_sessions(self) -> List[dict]:
        with self._lock:
            return [
                {
                    "session_id": item.session_id,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                    "turn_count": len(item.turns),
                }
                for item in self._sessions.values()
            ]

    def chat(self, session_id: str, message: str, exclude_tools: Optional[List[str]] = None) -> str:
        session = self.get_or_create(session_id)
        with session.lock:
            answer = session.agent.run_conversation(
                message,
                system_message=self.system_prompt,
                exclude_tools=exclude_tools,
            )
            session.updated_at = time.time()
            session.turns.append(ChatTurn(role="user", content=message))
            session.turns.append(ChatTurn(role="assistant", content=answer or ""))
            return answer or ""

    def _evict_if_needed_locked(self) -> None:
        if len(self._sessions) < self.max_sessions:
            return
        oldest_id = min(self._sessions.values(), key=lambda item: item.updated_at).session_id
        self._sessions.pop(oldest_id, None)
