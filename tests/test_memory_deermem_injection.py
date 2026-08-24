"""Phase 3 · DeerMemProvider 注入路径测试（从 facts 按预算渲染）。

覆盖：facts -> 注入文本含关键 content、超预算按 confidence 截断、guaranteed 类别保底、
空库返回空串、满足 MemoryProvider 契约。
"""

from core.memory_facts import FactStore
from core.memory_provider import (
    DeerMemProvider,
    MemoryProvider,
    format_facts_for_injection,
)


def _store_with(tmp_path, facts):
    store = FactStore(memory_dir=str(tmp_path))
    made = [
        store.make_fact(
            f["content"], category=f.get("category", "context"),
            confidence=f.get("confidence", 0.9), scope="user",
            durability="durable", authority="descriptive",
        )
        for f in facts
    ]
    store.write_all(made)
    return store


def test_deermem_provider_satisfies_protocol(tmp_path):
    store = FactStore(memory_dir=str(tmp_path))
    provider = DeerMemProvider(store=store, async_extract=False)
    assert isinstance(provider, MemoryProvider)  # runtime_checkable
    assert isinstance(provider.get_context(), str)
    assert "count" in provider.search("anything")


def test_get_context_empty_when_no_facts(tmp_path):
    provider = DeerMemProvider(store=FactStore(memory_dir=str(tmp_path)), async_extract=False)
    assert provider.get_context() == ""


def test_get_context_renders_facts(tmp_path):
    store = _store_with(tmp_path, [
        {"content": "用户偏好中文回复", "confidence": 0.9},
        {"content": "用户在用 macOS", "confidence": 0.8},
    ])
    provider = DeerMemProvider(store=store, async_extract=False)
    text = provider.get_context()
    assert "用户偏好中文回复" in text
    assert "用户在用 macOS" in text


def test_injection_truncates_by_confidence(tmp_path, monkeypatch):
    # 极小预算：只容得下最高置信度的一条。
    facts = [
        {"content": "高置信度事实内容占位" * 3, "confidence": 0.95},
        {"content": "低置信度事实内容占位" * 3, "confidence": 0.10},
    ]
    text = format_facts_for_injection(
        [
            {"content": f["content"], "confidence": f["confidence"], "category": "context"}
            for f in facts
        ],
        max_tokens=20, guaranteed_categories=[], guaranteed_token_budget=0,
    )
    assert "高置信度事实内容占位" in text
    assert "低置信度事实内容占位" not in text


def test_guaranteed_category_survives_tight_budget():
    facts = [
        {"content": "普通高置信度" * 5, "confidence": 0.99, "category": "context"},
        {"content": "纠正类记忆", "confidence": 0.3, "category": "correction"},
    ]
    # regular 预算为 0，但 correction 走 guaranteed 预算，应保留。
    text = format_facts_for_injection(
        facts, max_tokens=1, guaranteed_categories=["correction"], guaranteed_token_budget=500,
    )
    assert "纠正类记忆" in text
