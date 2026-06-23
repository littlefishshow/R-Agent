"""HTTP gateway for running R-Agent as a service."""

from .service import AgentSessionManager

__all__ = ["AgentSessionManager"]
