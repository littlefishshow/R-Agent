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
    assert n["metadata"] == {"dia_id": "D3:1", "speaker": "Melanie", "date": "2022"}


def test_extraction_prompt_requests_metadata():
    messages = build_extraction_messages([], [], frozenset())
    system = messages[0]["content"]
    assert "metadata" in system
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


def test_durable_fact_still_persists_alongside_session(tmp_path, monkeypatch):
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
    # durable 事实进 durable 库（也进 session 库，因为 session 不设 gate）。
    assert store.count() == 1
    assert provider._get_session_store().count() == 1
