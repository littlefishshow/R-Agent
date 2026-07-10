from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .debug import DebugLog
from .types import StepSpec, ToolSpec
from .utils import atomic_write_json


CHILD_ALLOWED_TOOLS = (
    "read_file",
    "search_files",
    "skill_search",
    "skill_view",
    "artifact_write",
    "read_eval",
    "command_status",
)


def delegate_task(
    *,
    root: str | Path,
    client,
    model: str,
    parent_step: str,
    goal: str,
    context: dict[str, Any] | None = None,
    max_iterations: int = 6,
    child_allowed_tools: list[str] | tuple[str, ...] | None = None,
    tools_factory,
    debug: DebugLog | None = None,
    trace_root: str | Path | None = None,
) -> dict[str, Any]:
    """Run a self-contained child AgentLoop and return a compact result.

    This is intentionally not R-Agent's delegate_task. It has no GUI/session
    coupling and no recursive delegation. The parent receives only a summary and
    artifact paths; the full child messages live in trace files.
    """
    root = Path(root).expanduser().resolve()
    trace_dir = Path(trace_root) if trace_root else root / ".autoresearch" / "child_traces"
    child_id = f"child-{int(time.time() * 1000)}"
    allowed = tuple(child_allowed_tools or CHILD_ALLOWED_TOOLS)
    if "delegate_task" in allowed:
        allowed = tuple(name for name in allowed if name != "delegate_task")
    child_tools = tools_factory(root)
    spec = StepSpec(
        name=f"delegate_{parent_step}",
        done_tag="DELEGATE_DONE",
        system_prompt=(
            "You are a child autoresearch agent. Handle only the delegated task. "
            "Do not assume access to the parent conversation. Use tools to gather "
            "evidence, then return a concise result. Do not call delegate_task."
        ),
        user_goal=goal,
        allowed_tools=allowed,
        context_files=(),
    )
    payload = {
        "child_id": child_id,
        "parent_step": parent_step,
        "goal": goal,
        "context": context or {},
        "created_at": time.time(),
        "allowed_tools": list(allowed),
    }
    child_context_path = root / ".autoresearch" / "child_contexts" / f"{child_id}.json"
    atomic_write_json(child_context_path, payload)
    from .agent import AgentLoop

    agent = AgentLoop(
        client=client,
        model=model,
        tools=child_tools,
        debug=debug or DebugLog(root, enabled=False),
        trace_dir=trace_dir,
    )
    result = agent.run_step(spec=spec, context=payload, max_iterations=max(1, int(max_iterations or 1)))
    record = {
        "child_id": child_id,
        "parent_step": parent_step,
        "goal": goal,
        "done": result.done,
        "iterations": result.iterations,
        "summary": str(result.content or result.error)[:2000],
        "error": result.error,
        "trace_path": result.trace_path,
        "context_path": str(child_context_path),
        "usage": result.token_usage,
        "stats": result.stats,
        "finished_at": time.time(),
    }
    result_path = root / ".autoresearch" / "child_results" / f"{child_id}.json"
    atomic_write_json(result_path, record)
    record["result_path"] = str(result_path)
    return record
