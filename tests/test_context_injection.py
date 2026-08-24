"""上下文管理测试（Improve_progress/03）。

覆盖本章已落地的两个点：
1. 当前日期注入进 system prompt（框架权限，可配置时区，跨天刷新）；
2. 上下文压缩时把摘要写入 ThreadState.summary_text channel。
"""

import json
from types import SimpleNamespace

import core.prompt_builder as pb
from core.agent import RAgent


# --------------------------------------------------------------------------- #
# 1. 日期注入
# --------------------------------------------------------------------------- #
def test_runtime_context_block_contains_date_and_timezone():
    block = pb.build_runtime_context_block()
    assert "Runtime context" in block
    assert "Current date:" in block
    # 默认时区 Asia/Shanghai
    assert "Asia/Shanghai" in block


def test_system_prompt_includes_current_date():
    sp = pb.build_system_prompt()
    assert "Current date:" in sp
    assert "Runtime context" in sp


def test_timezone_override(monkeypatch):
    monkeypatch.setenv("R_AGENT_TIMEZONE", "America/New_York")
    block = pb.build_runtime_context_block()
    assert "America/New_York" in block


def test_date_refreshes_on_rebuild(monkeypatch):
    """跨天时重新构建应反映新日期（用假 datetime 验证不缓存旧值）。"""
    import datetime as _dt

    class _FakeDT(_dt.datetime):
        _fixed = None

        @classmethod
        def now(cls, tz=None):
            return cls._fixed

    day1 = _dt.datetime(2026, 1, 1, 9, 0, 0)
    day2 = _dt.datetime(2026, 1, 2, 9, 0, 0)

    monkeypatch.setattr(pb, "datetime", _FakeDT)
    # 用本地时间分支（zoneinfo 对 fake datetime 不适用），强制走 fallback。
    monkeypatch.setenv("R_AGENT_TIMEZONE", "Invalid/Zone")

    _FakeDT._fixed = day1
    b1 = pb.build_runtime_context_block()
    _FakeDT._fixed = day2
    b2 = pb.build_runtime_context_block()

    assert "2026-01-01" in b1
    assert "2026-01-02" in b2
    assert b1 != b2


# --------------------------------------------------------------------------- #
# 2. summary_text channel
# --------------------------------------------------------------------------- #
class _FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **k: None))


def test_compression_populates_summary_text(monkeypatch):
    """触发压缩时应把摘要写入 state.summary_text。"""
    import core.agent as agent_mod

    agent = RAgent(model="test-model", max_iterations=2, enable_self_review=False)
    agent.client = _FakeClient()

    monkeypatch.setattr(agent_mod, "should_compress_context",
                        lambda *a, **k: {"should_compress": True, "estimated_tokens": 9000, "usage_ratio": 0.9})
    monkeypatch.setattr(agent_mod, "compress_messages",
                        lambda *a, **k: {"success": True, "compressed": True,
                                          "compressed_messages": [{"role": "system", "content": "【摘要】..."}],
                                          "summary": "【自动上下文压缩摘要】用户目标：X；关键决策：Y。",
                                          "stats": {"compressed_estimated_tokens": 3000, "usage_ratio_after": 0.3}})

    assert agent.state.summary_text == ""
    agent.messages = [{"role": "user", "content": "x" * 100}]
    agent._maybe_compress_context([])

    assert "自动上下文压缩摘要" in agent.state.summary_text
    assert "关键决策" in agent.state.summary_text


def test_no_summary_when_not_compressed(monkeypatch):
    """未触发压缩时 summary_text 保持为空。"""
    import core.agent as agent_mod

    agent = RAgent(model="test-model", max_iterations=2, enable_self_review=False)
    agent.client = _FakeClient()
    monkeypatch.setattr(agent_mod, "should_compress_context",
                        lambda *a, **k: {"should_compress": False, "estimated_tokens": 10, "usage_ratio": 0.01})

    agent.messages = [{"role": "user", "content": "short"}]
    agent._maybe_compress_context([])
    assert agent.state.summary_text == ""


# --------------------------------------------------------------------------- #
# 3. KV-cache 前缀稳定化：durable 快照冻结 + 压缩时刷新
# --------------------------------------------------------------------------- #
def test_durable_snapshot_is_frozen_until_compaction(monkeypatch):
    """durable 备份在压缩之间冻结：ledger 变化不改快照，压缩后才并入新摘要。"""
    monkeypatch.setenv("DURABLE_CONTEXT_ENABLED", "1")
    monkeypatch.setenv("MEMORY_INJECTION_MODE", "system")  # 排除记忆读盘的环境干扰

    agent = RAgent(model="test-model", max_iterations=2, enable_self_review=False)
    agent.state.summary_text = "摘要 A"

    first = agent._get_durable_snapshot()
    assert first is not None and "摘要 A" in first["content"]

    # 压缩之间即使派生通道变化，快照仍逐字节冻结（KV 友好）。
    agent.state.delegation_ledger = [{"task_id": "td9", "status": "done"}]
    second = agent._get_durable_snapshot()
    assert second is first
    assert "td9" not in second["content"]

    # 模拟一次压缩完成：摘要更新 + 统一刷新钩子重建快照。
    agent.state.summary_text = "摘要 B"
    agent._on_context_compacted()
    third = agent._get_durable_snapshot()
    assert third is not second
    assert "摘要 B" in third["content"]
    assert "td9" in third["content"]  # 压缩时才把最新 ledger 并入前缀


def test_tool_snapshot_grows_but_does_not_shrink_between_compactions(monkeypatch):
    """工具快照只增不减：提升后立即可见；收窄不动快照，压缩时才复位。"""
    monkeypatch.setenv("DEFERRED_TOOLS_ENABLED", "1")
    monkeypatch.setenv("DEFERRED_TOOLS_ALWAYS_ON", "tool_search")

    def _schema(name):
        return {"type": "function", "function": {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}}}

    agent = RAgent(model="test-model", max_iterations=2, enable_self_review=False)

    live = [_schema("tool_search")]
    snap1 = agent._stabilize_request_tools(live)
    assert {s["function"]["name"] for s in snap1} == {"tool_search"}

    # 提升 web_fetch（增长）：并入快照，且保持按 name 排序稳定。
    agent._promoted_tools.add("web_fetch")
    live2 = [_schema("tool_search"), _schema("web_fetch")]
    snap2 = agent._stabilize_request_tools(live2)
    names2 = [s["function"]["name"] for s in snap2]
    assert names2 == ["tool_search", "web_fetch"]

    # 收窄回只有 tool_search（收缩）：快照不缩小，web_fetch 仍在前缀（KV 稳定）。
    live3 = [_schema("tool_search")]
    snap3 = agent._stabilize_request_tools(live3)
    assert {s["function"]["name"] for s in snap3} == {"tool_search", "web_fetch"}

    # 压缩后复位：下一轮从当前作用域现算，收窄由此生效。
    agent._on_context_compacted()
    snap4 = agent._stabilize_request_tools(live3)
    assert {s["function"]["name"] for s in snap4} == {"tool_search"}


def test_deferred_tool_denied_backstops_unpromoted_calls(monkeypatch):
    """执行层保底闸门：延迟暴露开启时，未提升且非 always-on 的工具被拒。"""
    monkeypatch.setenv("DEFERRED_TOOLS_ENABLED", "1")
    monkeypatch.setenv("DEFERRED_TOOLS_ALWAYS_ON", "tool_search,read_file")

    agent = RAgent(model="test-model", max_iterations=2, enable_self_review=False)
    assert agent._deferred_tool_denied("web_fetch") is True   # 未提升 -> 拒
    assert agent._deferred_tool_denied("read_file") is False   # always-on -> 放行
    agent._promoted_tools.add("web_fetch")
    assert agent._deferred_tool_denied("web_fetch") is False   # 提升后 -> 放行


def test_deferred_tool_denied_off_allows_everything(monkeypatch):
    """延迟暴露关闭时保底闸门不生效，全量放行（零行为变化）。"""
    monkeypatch.delenv("DEFERRED_TOOLS_ENABLED", raising=False)
    agent = RAgent(model="test-model", max_iterations=2, enable_self_review=False)
    assert agent._deferred_tool_denied("anything") is False
