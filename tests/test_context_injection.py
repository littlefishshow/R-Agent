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
