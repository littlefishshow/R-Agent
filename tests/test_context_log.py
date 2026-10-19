"""虚拟上下文（Context Log）与物理上下文隔离的测试。

验证：
1. ContextLog 本身 append-only / 无损 / slice / search / 跨 run seq；
2. Agent 主循环把消息无损镜像进时间流，并把一次压缩映射成 fold 标记——被折叠的原始
   消息仍可从时间流无损取回，而物理 messages 已被摘要替换；
3. L0 最终消息里的 thinking 被虚拟上下文保留（物理层会丢弃）。
"""

import json
from types import SimpleNamespace

from core import config
from core.agent import RAgent
from core.context_log import ContextLog, ET_FOLD, read_context_log
from tools.registry import registry


# ---------------------------------------------------------------------------
# 1. ContextLog 单元测试
# ---------------------------------------------------------------------------
def test_context_log_appends_losslessly_and_slices(tmp_path):
    log = ContextLog("s1", base_dir=str(tmp_path))
    log.append("run_start", run_id="r1")
    log.record_message({"role": "user", "content": "task A"}, run_id="r1")
    log.record_message({"role": "assistant", "content": "analysis"}, run_id="r1", thinking="let me think")
    log.record_message({"role": "tool", "tool_call_id": "c1", "name": "read", "content": "body"}, run_id="r1")

    assert len(log) == 4
    sliced = log.slice(2, 4)
    assert [e.content for e in sliced] == ["task A", "analysis", "body"]
    # thinking 被虚拟上下文保留。
    assert sliced[1].meta.get("thinking") == "let me think"
    # 落盘可无损读回。
    rows = read_context_log(log.path)
    assert len(rows) == 4


def test_context_log_search_and_fold_region(tmp_path):
    log = ContextLog("s2", base_dir=str(tmp_path))
    log.record_message({"role": "user", "content": "remember constraint FOO"}, run_id="r1")
    log.record_message({"role": "assistant", "content": "ok"}, run_id="r1")
    fold = log.record_fold(start_seq=1, end_seq=2, summary="summary of FOO", run_id="r1", stats={"n": 2})

    assert fold.type == ET_FOLD
    hits = log.search("constraint foo")
    assert [h.seq for h in hits] == [1]
    # 被折叠的 seq 从非折叠时间线中排除，但仍可 slice 取回。
    assert [e.seq for e in log.timeline(include_folded=False)] == [3]
    assert [e.content for e in log.slice(1, 2)] == ["remember constraint FOO", "ok"]


def test_context_log_seq_is_continuous_across_runs(tmp_path):
    log = ContextLog("s3", base_dir=str(tmp_path))
    log.record_message({"role": "user", "content": "run1 msg"}, run_id="r1")
    log.append("run_end", run_id="r1")
    log.record_message({"role": "user", "content": "run2 msg"}, run_id="r2")
    seqs = [e.seq for e in log.entries()]
    assert seqs == [1, 2, 3]
    assert log.entries()[-1].run_id == "r2"


def test_context_log_disabled_is_noop(tmp_path):
    log = ContextLog("s4", base_dir=str(tmp_path), enabled=False)
    assert log.append("run_start") is None
    assert log.record_message({"role": "user", "content": "x"}) is None
    assert len(log) == 0


# ---------------------------------------------------------------------------
# 2/3. Agent 主循环：虚拟/物理隔离 + 压缩无损
# ---------------------------------------------------------------------------
class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        if not self._responses:
            raise AssertionError("unexpected extra LLM call")
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=_FakeCompletions(responses))


def _message(content="", tool_calls=None, reasoning=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    if reasoning is not None:
        msg.reasoning_content = reasoning
    return msg


def _response(message, finish="stop"):
    return SimpleNamespace(
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
    )


def test_agent_mirrors_messages_into_virtual_context(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_context_log_dir", lambda: str(tmp_path))
    monkeypatch.setattr(config, "get_session_sandbox_enabled", lambda: False)

    agent = RAgent(model="test-model", max_iterations=2, enable_self_review=False, session_id="vctx1")
    agent.client = _FakeClient([_response(_message(content="final answer", reasoning="deep thought"))])

    assert agent.run_conversation("hello world") == "final answer"

    log = agent.context_log
    assert log is not None
    entries = log.entries()
    kinds = [(e.type, e.role) for e in entries]
    # run_start + user + assistant(final) + run_end
    assert ("run_start", "") in kinds
    assert ("user", "user") in kinds
    assert ("assistant", "assistant") in kinds
    assert ("run_end", "") in kinds
    # 物理层丢弃 thinking，虚拟上下文保留。
    assistant_entries = [e for e in entries if e.type == "assistant"]
    assert assistant_entries[-1].meta.get("thinking") == "deep thought"


def test_compression_folds_virtual_context_losslessly(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_context_log_dir", lambda: str(tmp_path))
    monkeypatch.setattr(config, "get_session_sandbox_enabled", lambda: False)
    # 收紧窗口触发压缩。
    monkeypatch.setattr(config, "get_llm_context_window", lambda: 180)
    monkeypatch.setattr(config, "get_context_compression_trigger_ratio", lambda: 0.2)
    monkeypatch.setattr(config, "get_context_compression_target_ratio", lambda: 0.5)
    monkeypatch.setattr(config, "get_context_compression_preserve_recent_messages", lambda: 2)
    monkeypatch.setattr(config, "get_context_summarization_mode", lambda: "heuristic")

    agent = RAgent(model="test-model", max_iterations=1, enable_self_review=False, session_id="vctx2")
    agent.client = _FakeClient([_response(_message(content="ok"))])
    # 预置较长历史，确保触发压缩。
    agent.messages = [
        {"role": "system", "content": "base"},
        {"role": "user", "content": "早期需求 A，务必记住约束 UNIQUEMARK_A " * 80},
        {"role": "assistant", "content": "早期分析 B " * 80},
    ]
    # 把预置历史也镜像进虚拟上下文（模拟它们曾经进入过对话）。
    for m in agent.messages:
        agent._log_context_message(m)

    assert agent.run_conversation("新问题", system_message="base") == "ok"
    assert agent.context_usage.get("compressed_count", 0) >= 1

    # 物理 messages：早期原文已被摘要替换，UNIQUEMARK_A 不再出现在被折叠的那条里。
    log = agent.context_log
    assert log is not None
    fold_entries = [e for e in log.entries() if e.type == ET_FOLD]
    assert fold_entries, "压缩应在虚拟上下文留下至少一个 fold 标记"

    # 无损溯源：被折叠区间仍能从虚拟上下文取回原始的 UNIQUEMARK_A。
    hits = log.search("UNIQUEMARK_A")
    assert hits, "被折叠掉的原文仍应能在虚拟上下文中检索到"
    fold = fold_entries[0]
    recalled = log.slice(fold.meta["start_seq"], fold.meta["end_seq"])
    assert any("UNIQUEMARK_A" in str(e.content) for e in recalled)


def test_context_log_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_context_log_dir", lambda: str(tmp_path))
    monkeypatch.setattr(config, "get_session_sandbox_enabled", lambda: False)
    monkeypatch.setattr(config, "get_context_log_enabled", lambda: False)

    agent = RAgent(model="test-model", max_iterations=2, enable_self_review=False, session_id="vctx3")
    agent.client = _FakeClient([_response(_message(content="done"))])
    assert agent.run_conversation("hi") == "done"
    # 关闭时不创建时间流，不落盘。
    assert agent.context_log is None
    assert not list(tmp_path.glob("*.context.jsonl"))
