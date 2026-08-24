from core import config
from core.agent import RAgent


def test_archive_subtask_compresses_messages_with_unified_summary(monkeypatch):
    # durable-off 变体：摘要以 system 消息回注 messages。
    monkeypatch.setenv("MEMORY_INJECTION_MODE", "system")
    monkeypatch.setenv("DURABLE_CONTEXT_ENABLED", "0")
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


def test_archive_subtask_routes_summary_to_durable_by_default(monkeypatch):
    """durable-on（默认）：归档摘要进 summary_text 通道，不再塞进 messages。"""
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
    assert agent.messages[-1]["content"] == "latest"
    # 摘要落在 durable 通道，并已重建为 durable 快照。
    assert "done" in agent.state.summary_text
    snapshot = agent._get_durable_snapshot()
    assert snapshot is not None and "done" in snapshot["content"]
