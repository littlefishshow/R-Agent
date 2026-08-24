"""运行事件流（Run Event Stream）测试。

覆盖 Improve_progress/08_运行事件流.md 的验证点：
1. 一次含工具调用的最小会话，事件按序落盘且 seq 严格递增；
2. 上下文压缩会产生 context.compact 事件；
3. store 写入失败时主循环仍正常完成（降级安全）；
4. 事件流可通过环境变量关闭。
"""

import json
from pathlib import Path
from types import SimpleNamespace

from core import events as run_events
from core.agent import RAgent
from tools.registry import registry


# --- 复用仓库现有的假 LLM 客户端形态（见 tests/test_agent_large_tool_output.py） ---
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


def _tool_call(name, arguments):
    return SimpleNamespace(
        id=f"call_{name}",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)),
    )


def _message(content="", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _response(message):
    return SimpleNamespace(usage={"total_tokens": 1}, choices=[SimpleNamespace(message=message)])


# --------------------------------------------------------------------------- #
def test_event_store_records_ordered_run(monkeypatch, tmp_path):
    """一次“调模型 + 用一次工具 + 收尾”的会话应产生按序、seq 递增的事件流。"""
    monkeypatch.chdir(tmp_path)

    def echo_tool():
        return "ok"

    registry.register("echo_tool_for_events_test", "echo", {"type": "object", "properties": {}}, echo_tool)
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [registry._tools["echo_tool_for_events_test"]["schema"]])
    monkeypatch.setattr(registry, "execute_tool_isolated", lambda name, args, **kwargs: registry.execute_tool(name, args))

    agent = RAgent(model="test-model", max_iterations=3, enable_self_review=False)
    agent.client = _FakeClient([
        _response(_message(tool_calls=[_tool_call("echo_tool_for_events_test", {})])),
        _response(_message(content="done", tool_calls=None)),
    ])

    assert agent.run_conversation("go") == "done"

    path = agent.event_store.path
    rows = run_events.read_events(path)
    seqs = [r["seq"] for r in rows]
    types = [r["event_type"] for r in rows]

    # seq 严格递增
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)

    # 关键事件都在，且首尾为 run.start / run.end
    assert types[0] == run_events.EV_RUN_START
    assert types[-1] == run_events.EV_RUN_END
    for expected in (
        run_events.EV_LLM_REQUEST,
        run_events.EV_LLM_RESPONSE,
        run_events.EV_TOOL_CALL,
        run_events.EV_TOOL_RESULT,
    ):
        assert expected in types, f"missing event {expected}"

    # tool.call 出现在 tool.result 之前
    assert types.index(run_events.EV_TOOL_CALL) < types.index(run_events.EV_TOOL_RESULT)

    # category 映射正确
    by_type = {r["event_type"]: r for r in rows}
    assert by_type[run_events.EV_TOOL_CALL]["category"] == "trace"
    assert by_type[run_events.EV_RUN_ERROR if False else run_events.EV_RUN_END]["category"] == "outputs"


def test_context_compact_event_emitted(monkeypatch, tmp_path):
    """触发压缩时应写入 context.compact 事件并带前后 token 数。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [])

    agent = RAgent(model="test-model", max_iterations=2, enable_self_review=False)
    agent.client = _FakeClient([_response(_message(content="answer", tool_calls=None))])

    # 直接构造一次压缩，验证事件（避免依赖真实 token 阈值）。
    agent.event_store = run_events.RunEventStore(
        run_id="compact-test", base_dir=str(tmp_path / "ev"), enabled=True
    )
    import core.agent as agent_mod

    monkeypatch.setattr(agent_mod, "should_compress_context",
                        lambda *a, **k: {"should_compress": True, "estimated_tokens": 9000, "usage_ratio": 0.9})
    monkeypatch.setattr(agent_mod, "compress_messages",
                        lambda *a, **k: {"success": True, "compressed": True,
                                          "compressed_messages": [{"role": "system", "content": "summary"}],
                                          "stats": {"compressed_estimated_tokens": 3000, "usage_ratio_after": 0.3}})
    agent.messages = [{"role": "user", "content": "x" * 100}]
    agent._maybe_compress_context([])

    rows = run_events.read_events(agent.event_store.path)
    compact = [r for r in rows if r["event_type"] == run_events.EV_CONTEXT_COMPACT]
    assert len(compact) == 1
    assert compact[0]["content"]["before_tokens"] == 9000
    assert compact[0]["content"]["after_tokens"] == 3000


def test_store_write_failure_does_not_break_loop(monkeypatch, tmp_path):
    """事件写入失败时，主循环仍应正常完成（只降级为静默 warning）。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [])

    agent = RAgent(model="test-model", max_iterations=2, enable_self_review=False)
    agent.client = _FakeClient([_response(_message(content="ok", tool_calls=None))])

    # 让所有 emit 抛异常，模拟磁盘不可写。
    def _boom(self, *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(run_events.RunEventStore, "emit", _boom)

    # 主循环不应因此崩溃。
    assert agent.run_conversation("hello") == "ok"


def test_events_can_be_disabled(monkeypatch, tmp_path):
    """RUN_EVENTS_ENABLED=0 时不应产生事件文件。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_EVENTS_ENABLED", "0")
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [])

    agent = RAgent(model="test-model", max_iterations=2, enable_self_review=False)
    agent.client = _FakeClient([_response(_message(content="ok", tool_calls=None))])

    assert agent.run_conversation("hello") == "ok"
    assert agent.event_store is not None
    assert agent.event_store.enabled is False
    assert not Path(agent.event_store.path).exists()


def test_run_events_default_dir_is_global_sandbox(monkeypatch, tmp_path):
    """默认（未启用 per-session 沙箱）时事件仍落在全局 sandbox/run_events。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SESSION_SANDBOX_ENABLED", raising=False)
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [])

    agent = RAgent(model="test-model", max_iterations=2, enable_self_review=False, session_id="sess-legacy")
    agent.client = _FakeClient([_response(_message(content="ok", tool_calls=None))])

    assert agent.run_conversation("hello") == "ok"
    event_path = Path(agent.event_store.path)
    assert event_path.exists()
    assert event_path.parent == Path("sandbox") / "run_events"


def test_run_events_migrate_to_per_session_sandbox(monkeypatch, tmp_path):
    """启用 per-session 沙箱后，事件流落到 <session-root>/run_events 下，实现按会话隔离。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SESSION_SANDBOX_ENABLED", "1")
    monkeypatch.setenv("SESSION_SANDBOX_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [])

    agent = RAgent(model="test-model", max_iterations=2, enable_self_review=False, session_id="gui/a")
    agent.client = _FakeClient([_response(_message(content="ok", tool_calls=None))])

    assert agent.run_conversation("hello") == "ok"
    event_path = Path(agent.event_store.path)
    assert event_path.exists()
    # 事件落在该 session 的沙箱根下，而非全局 sandbox/run_events
    assert event_path.parent == (tmp_path / "sessions" / "gui_a" / "run_events")
    assert not (tmp_path / "sandbox" / "run_events").exists()
