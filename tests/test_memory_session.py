"""Session 级情节记忆测试：细粒度 + 溯源 metadata + 可检索 + session 结束即消失。

覆盖：
- make_fact 保留 metadata（dia_id/session/date/speaker）；
- session_fact_store / safe_session_id / clear / delete_store；
- 抽取器渲染每轮 metadata、_normalize_fact 保留 fact.metadata；
- DeerMemProvider：transient 事实进不了 durable 库但进 session 库、search 合并 durable+session
  且返回 metadata、end_session 后消失、默认开关。
"""

import json
import types

from core.memory_facts import FactStore, safe_session_id, session_fact_store
from core.memory_extractor import (
    MemoryExtractor,
    build_extraction_messages,
    _normalize_fact,
    _format_conversation,
    _validate_update_provenance,
)
from core.memory_provider import DeerMemProvider


# --------------------------------------------------------------------------- #
# FactStore metadata + session 存储层
# --------------------------------------------------------------------------- #
def test_make_fact_keeps_metadata(tmp_path):
    store = FactStore(memory_dir=str(tmp_path))
    f = store.make_fact(
        "Caroline 在 2023-05-07 参加了 LGBTQ 支持团体",
        category="context", confidence=0.9,
        metadata={"dia_id": "D1:2", "speaker": "Caroline", "date": "2023-05-07", "empty": "  "},
    )
    assert f["metadata"] == {"dia_id": "D1:2", "speaker": "Caroline", "date": "2023-05-07"}


def test_make_fact_keeps_source_turn_ids(tmp_path):
    store = FactStore(memory_dir=str(tmp_path))
    f = store.make_fact(
        "新鞋用于跑步",
        metadata={
            "source_turn_ids": ["D7:18", "D7:19", "D7:19", ""],
            "primary_turn_id": "D7:19",
            "source_quote": "these are for running",
        },
    )
    assert f["metadata"]["source_turn_ids"] == ["D7:18", "D7:19"]
    assert f["metadata"]["primary_turn_id"] == "D7:19"


def test_safe_session_id():
    assert safe_session_id("conv/../1") == "conv_.._1" or ".." not in safe_session_id("conv/../1")
    assert safe_session_id("") == "default"
    assert safe_session_id("  ") == "default"
    assert safe_session_id("locomo-1") == "locomo-1"


def test_session_store_isolated_and_clearable(tmp_path):
    s1 = session_fact_store("conv-1", memory_dir=str(tmp_path))
    s2 = session_fact_store("conv-2", memory_dir=str(tmp_path))
    assert s1.facts_file != s2.facts_file
    s1.append_fact(s1.make_fact("会话1事实"))
    s2.append_fact(s2.make_fact("会话2事实"))
    assert s1.count() == 1 and s2.count() == 1
    s1.clear()
    assert s1.count() == 0
    assert s2.count() == 1  # 互不影响


def test_delete_store_removes_file(tmp_path):
    s = session_fact_store("conv-x", memory_dir=str(tmp_path))
    s.append_fact(s.make_fact("待删除事实"))
    import os
    assert os.path.exists(s.facts_file)
    s.delete_store()
    assert not os.path.exists(s.facts_file)


# --------------------------------------------------------------------------- #
# 抽取器：溯源字段
# --------------------------------------------------------------------------- #
def test_conversation_renders_turn_metadata():
    conv = [
        {"role": "user", "content": "我参加了支持团体", "dia_id": "D1:2",
         "speaker": "Caroline", "date": "2023-05-07"},
    ]
    text = _format_conversation(conv)
    assert "Caroline" in text
    assert "dia_id=D1:2" in text
    assert "date=2023-05-07" in text


def test_normalize_fact_preserves_metadata():
    raw = {
        "content": "Melanie 在 2022 画了日出", "category": "context", "confidence": 0.8,
        "scope": "user", "durability": "transient", "authority": "descriptive",
        "metadata": {"dia_id": "D3:1", "speaker": "Melanie", "date": "2022"},
    }
    n = _normalize_fact(raw)
    assert n["metadata"] == {
        "dia_id": "D3:1",
        "source_turn_ids": ["D3:1"],
        "primary_turn_id": "D3:1",
        "speaker": "Melanie",
        "date": "2022",
    }


def test_normalize_fact_splits_legacy_multi_source_dia_id():
    n = _normalize_fact({
        "content": "多轮事实",
        "category": "context",
        "confidence": 0.8,
        "scope": "user",
        "durability": "transient",
        "authority": "descriptive",
        "metadata": {"dia_id": "D20:6; D20:8"},
    })
    assert n["metadata"]["source_turn_ids"] == ["D20:6", "D20:8"]
    assert n["metadata"]["primary_turn_id"] == "D20:6"
    assert n["metadata"]["dia_id"] == "D20:6"


def test_provenance_quote_realigns_primary_turn():
    update = {
        "newFacts": [{
            "content": "Melanie 的新鞋用于跑步",
            "metadata": {
                "source_turn_ids": ["D7:18", "D99:1"],
                "primary_turn_id": "D7:18",
                "source_quote": "these are for running",
            },
        }],
    }
    conversation = [
        {
            "role": "user",
            "content": "I just got some new shoes.",
            "metadata": {
                "dia_id": "D7:18",
                "session": "session_7",
                "speaker": "Melanie",
                "date": "2023-07-12",
            },
        },
        {
            "role": "assistant",
            "content": "These are for running.",
            "metadata": {
                "dia_id": "D7:19",
                "session": "session_7",
                "speaker": "Melanie",
                "date": "2023-07-12",
            },
        },
    ]
    validated = _validate_update_provenance(update, conversation)
    metadata = validated["newFacts"][0]["metadata"]
    assert metadata["source_turn_ids"] == ["D7:18", "D7:19"]
    assert metadata["primary_turn_id"] == "D7:19"
    assert metadata["dia_id"] == "D7:19"
    assert metadata["source_quote"] == "these are for running"
    assert metadata["speaker"] == "Melanie"


def test_extraction_prompt_requests_metadata():
    messages = build_extraction_messages([], [], frozenset())
    system = messages[0]["content"]
    assert "metadata" in system
    assert "source_turn_ids" in system
    assert "primary_turn_id" in system
    assert "source_quote" in system
    assert "dia_id" in system and "speaker" in system


# --------------------------------------------------------------------------- #
# DeerMemProvider：session 情节记忆端到端
# --------------------------------------------------------------------------- #
def _mock_extractor(fact_dict):
    resp = json.dumps({"user": {}, "history": {}, "newFacts": [fact_dict], "factsToRemove": []})

    class _Msg:
        content = resp

    class _Ch:
        message = _Msg()

    class _R:
        choices = [_Ch()]

    def create(**k):
        return _R()

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
    return MemoryExtractor(client=client, model="m")


_EPISODIC_FACT = {
    "content": "Caroline 在 2023-05-07 参加了 LGBTQ 支持团体",
    "category": "context", "confidence": 0.9,
    "scope": "user", "durability": "transient", "authority": "descriptive",
    "metadata": {"dia_id": "D1:2", "speaker": "Caroline", "date": "2023-05-07"},
}


def test_transient_fact_goes_to_session_not_durable(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_SESSION_FACTS_ENABLED", "1")
    store = FactStore(memory_dir=str(tmp_path))
    provider = DeerMemProvider(
        store=store, extractor=_mock_extractor(_EPISODIC_FACT),
        async_extract=False, memory_dir=str(tmp_path),
    )
    provider.set_session("conv-1")
    provider.add(thread_id="conv-1", messages=[
        {"role": "user", "content": "我 2023-05-07 参加了支持团体", "dia_id": "D1:2",
         "speaker": "Caroline", "date": "2023-05-07"},
        {"role": "assistant", "content": "记下了"},
    ])
    # durable 库：transient 被 scope gate 拒。
    assert store.count() == 0
    # session 库：保留细节 + metadata。
    sess = provider._get_session_store()
    facts = sess.load_facts()
    assert len(facts) == 1
    assert facts[0]["metadata"]["dia_id"] == "D1:2"


def test_search_merges_session_and_returns_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_SESSION_FACTS_ENABLED", "1")
    store = FactStore(memory_dir=str(tmp_path))
    provider = DeerMemProvider(
        store=store, extractor=_mock_extractor(_EPISODIC_FACT),
        async_extract=False, memory_dir=str(tmp_path),
    )
    provider.set_session("conv-1")
    provider.add(thread_id="conv-1", messages=[
        {"role": "user", "content": "我 2023-05-07 参加了支持团体", "dia_id": "D1:2",
         "speaker": "Caroline", "date": "2023-05-07"},
        {"role": "assistant", "content": "记下了"},
    ])
    result = provider.search("支持团体", top_k=5)
    assert result["count"] == 1
    r0 = result["results"][0]
    assert r0["metadata"]["speaker"] == "Caroline"
    assert r0["metadata"]["date"] == "2023-05-07"


def test_end_session_makes_episodic_facts_vanish(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_SESSION_FACTS_ENABLED", "1")
    store = FactStore(memory_dir=str(tmp_path))
    provider = DeerMemProvider(
        store=store, extractor=_mock_extractor(_EPISODIC_FACT),
        async_extract=False, memory_dir=str(tmp_path),
    )
    provider.set_session("conv-1")
    provider.add(thread_id="conv-1", messages=[
        {"role": "user", "content": "我 2023-05-07 参加了支持团体", "dia_id": "D1:2"},
        {"role": "assistant", "content": "记下了"},
    ])
    assert provider.search("支持团体")["count"] == 1
    provider.end_session()
    # session 结束 -> 情节记忆消失，检索不到。
    assert provider.search("支持团体")["count"] == 0


def test_session_facts_disabled_by_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_SESSION_FACTS_ENABLED", "0")
    store = FactStore(memory_dir=str(tmp_path))
    provider = DeerMemProvider(
        store=store, extractor=_mock_extractor(_EPISODIC_FACT),
        async_extract=False, memory_dir=str(tmp_path),
    )
    provider.set_session("conv-1")
    provider.add(thread_id="conv-1", messages=[
        {"role": "user", "content": "我 2023-05-07 参加了支持团体", "dia_id": "D1:2"},
        {"role": "assistant", "content": "记下了"},
    ])
    sess = provider._get_session_store()
    # 关闭时不写 session 事实。
    assert sess.count() == 0
    assert store.count() == 0


def test_durable_fact_only_persists_in_durable_store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_SESSION_FACTS_ENABLED", "1")
    monkeypatch.setenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.0")
    durable = {
        "content": "用户偏好中文回复", "category": "preference", "confidence": 0.9,
        "scope": "user", "durability": "durable", "authority": "descriptive",
    }
    store = FactStore(memory_dir=str(tmp_path))
    provider = DeerMemProvider(
        store=store, extractor=_mock_extractor(durable),
        async_extract=False, memory_dir=str(tmp_path),
    )
    provider.set_session("conv-1")
    provider.add(thread_id="conv-1", messages=[
        {"role": "user", "content": "我偏好中文回复"},
        {"role": "assistant", "content": "好的"},
    ])
    # durable 事实只进 durable 库；当前 session 搜索仍会合并 durable store。
    assert store.count() == 1
    assert provider._get_session_store().count() == 0
    assert provider.search("中文回复", thread_id="conv-1")["count"] == 1


def test_project_and_task_transient_facts_enter_session_store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_SESSION_FACTS_ENABLED", "1")
    monkeypatch.setenv("MEMORY_SESSION_FACT_CONFIDENCE_THRESHOLD", "0.3")
    store = FactStore(memory_dir=str(tmp_path))
    provider = DeerMemProvider(
        store=store,
        extractor=None,
        async_extract=False,
        memory_dir=str(tmp_path),
    )
    provider.set_session("conv-work")
    update = {
        "newFacts": [
            {
                "content": "当前项目的 durable store 尚未按 project namespace 隔离",
                "category": "constraint",
                "confidence": 0.9,
                "scope": "project",
                "durability": "transient",
                "authority": "descriptive",
            },
            {
                "content": "当前任务已定位到 _apply_session_facts 准入过窄",
                "category": "verified_result",
                "confidence": 0.8,
                "scope": "task",
                "durability": "transient",
                "authority": "descriptive",
            },
        ],
    }

    assert provider._apply_session_facts(update, "conv-work") == 2
    facts = provider._get_session_store().load_facts()
    assert {fact["scope"] for fact in facts} == {"project", "task"}
    assert store.count() == 0


def test_session_gate_rejects_unknown_scope_durable_and_imperative(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_SESSION_FACTS_ENABLED", "1")
    provider = DeerMemProvider(
        store=FactStore(memory_dir=str(tmp_path)),
        extractor=None,
        async_extract=False,
        memory_dir=str(tmp_path),
    )
    provider.set_session("conv-gate")
    base = {
        "category": "context",
        "confidence": 0.9,
        "durability": "transient",
        "authority": "descriptive",
    }
    update = {
        "newFacts": [
            {**base, "content": "未知 scope", "scope": "workspace"},
            {**base, "content": "项目 durable", "scope": "project", "durability": "durable"},
            {**base, "content": "任务命令", "scope": "task", "authority": "imperative"},
        ],
    }

    assert provider._apply_session_facts(update, "conv-gate") == 0
    assert provider._get_session_store().count() == 0


def test_session_confidence_threshold_is_independent_from_durable(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_SESSION_FACTS_ENABLED", "1")
    monkeypatch.setenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.8")
    monkeypatch.setenv("MEMORY_SESSION_FACT_CONFIDENCE_THRESHOLD", "0.3")
    provider = DeerMemProvider(
        store=FactStore(memory_dir=str(tmp_path)),
        extractor=None,
        async_extract=False,
        memory_dir=str(tmp_path),
    )
    provider.set_session("conv-confidence")
    update = {
        "newFacts": [
            {
                "content": "低于 session 阈值",
                "category": "context",
                "confidence": 0.2,
                "scope": "task",
                "durability": "transient",
                "authority": "descriptive",
            },
            {
                "content": "高于 session 但低于 durable 阈值",
                "category": "verified_result",
                "confidence": 0.4,
                "scope": "task",
                "durability": "transient",
                "authority": "descriptive",
            },
        ],
    }

    assert provider._apply_session_facts(update, "conv-confidence") == 1
    assert [fact["content"] for fact in provider._get_session_store().load_facts()] == [
        "高于 session 但低于 durable 阈值"
    ]


def test_session_capacity_prioritizes_operational_facts_and_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_SESSION_FACTS_ENABLED", "1")
    monkeypatch.setenv("MEMORY_SESSION_FACT_CONFIDENCE_THRESHOLD", "0.0")
    monkeypatch.setenv("MEMORY_SESSION_MAX_FACTS", "10")
    provider = DeerMemProvider(
        store=FactStore(memory_dir=str(tmp_path)),
        extractor=None,
        async_extract=False,
        memory_dir=str(tmp_path),
    )
    provider.set_session("conv-capacity")
    ordinary = [
        {
            "content": f"普通上下文 {index}",
            "category": "context",
            "confidence": 0.99,
            "scope": "task",
            "durability": "transient",
            "authority": "descriptive",
        }
        for index in range(10)
    ]
    important = [
        {
            "content": "必须保持 API 向后兼容",
            "category": "constraint",
            "confidence": 0.4,
            "scope": "project",
            "durability": "transient",
            "authority": "descriptive",
        },
        {
            "content": "测试已确认 task scope 可以检索",
            "category": "verified_result",
            "confidence": 0.4,
            "scope": "task",
            "durability": "transient",
            "authority": "descriptive",
            "metadata": {"source_turn_ids": ["D1:2"], "primary_turn_id": "D1:2"},
        },
    ]

    assert provider._apply_session_facts({"newFacts": ordinary + important}, "conv-capacity") == 12
    facts = provider._get_session_store().load_facts()
    assert len(facts) == 10
    contents = {fact["content"] for fact in facts}
    assert "必须保持 API 向后兼容" in contents
    assert "测试已确认 task scope 可以检索" in contents
