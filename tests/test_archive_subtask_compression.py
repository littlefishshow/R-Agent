from core import config
from core.agent import RAgent


def test_archive_subtask_compresses_messages_with_unified_summary(monkeypatch):
    monkeypatch.setattr(config, "get_context_summarization_mode", lambda: "heuristic")
    agent = RAgent(model="test", max_iterations=2)
    agent.messages = [
        {"role": "system", "content": "base"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "long"},
        {"role": "tool", "content": "detail"},
        {"role": "user", "content": "latest"},
    ]
    agent._compress_after_archive("done", "next")

    assert agent.messages[0]["content"] == "base"
    assert any("done" in (m.get("content") or "") for m in agent.messages if isinstance(m, dict))
    assert any("自动上下文压缩摘要" in (m.get("content") or "") for m in agent.messages if isinstance(m, dict))
    assert agent.messages[-1]["content"] == "latest"
