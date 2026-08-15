"""Phase 4 · DeerMemProvider 检索测试（SQLite FTS5 + 子串 fallback）。

覆盖：FTS 命中、confidence 排序、空查询/空库、子串 fallback、memory_search 工具分派。
"""

import json

from core.memory_facts import FactStore
from core.memory_provider import DeerMemProvider


def _store_with(tmp_path, items):
    store = FactStore(memory_dir=str(tmp_path))
    facts = [
        store.make_fact(content, confidence=conf, scope="user",
                        durability="durable", authority="descriptive")
        for content, conf in items
    ]
    store.write_all(facts)
    return store


def test_search_empty_query_and_empty_store(tmp_path):
    provider = DeerMemProvider(store=FactStore(memory_dir=str(tmp_path)), async_extract=False)
    assert provider.search("")["count"] == 0
    assert provider.search("anything")["count"] == 0


def test_fts_search_finds_relevant(tmp_path):
    store = _store_with(tmp_path, [
        ("用户偏好使用 Python 开发", 0.9),
        ("用户喜欢喝咖啡", 0.8),
        ("项目使用 Rust 编写核心模块", 0.7),
    ])
    provider = DeerMemProvider(store=store, async_extract=False)
    result = provider.search("Python", top_k=5)
    assert result["count"] >= 1
    contents = [r["content"] for r in result["results"]]
    assert any("Python" in c for c in contents)
    # 返回结构化字段
    assert "confidence" in result["results"][0]
    assert "category" in result["results"][0]


def test_substring_fallback_orders_by_confidence(tmp_path):
    store = _store_with(tmp_path, [
        ("macOS 环境低置信", 0.2),
        ("macOS 环境高置信", 0.95),
    ])
    provider = DeerMemProvider(store=store, async_extract=False)
    # 直接测子串路径
    results = provider._substring_search("macOS", 5)
    assert results[0]["content"] == "macOS 环境高置信"
    assert results[0]["confidence"] >= results[1]["confidence"]


# --------------------------------------------------------------------------- #
# 修复 3：中文检索（不再只做整句 substring）
# --------------------------------------------------------------------------- #
def test_chinese_phrase_query_matches_episodic_fact(tmp_path):
    store = _store_with(tmp_path, [
        ("Caroline 参加了一次支持团体活动", 0.9),
        ("Melanie 在 2022 画了日出", 0.8),
        ("用户喜欢喝咖啡", 0.7),
    ])
    provider = DeerMemProvider(store=store, async_extract=False)
    # 整句不是任何 fact 的子串，但按 CJK bigram 应命中"支持团体"那条。
    result = provider.search("参加支持团体", top_k=5)
    assert result["count"] >= 1
    assert result["results"][0]["content"] == "Caroline 参加了一次支持团体活动"


def test_chinese_single_word_query(tmp_path):
    store = _store_with(tmp_path, [
        ("Melanie 在 2022 画了日出", 0.8),
        ("用户喜欢喝咖啡", 0.7),
    ])
    provider = DeerMemProvider(store=store, async_extract=False)
    result = provider.search("日出", top_k=5)
    assert result["count"] == 1
    assert "日出" in result["results"][0]["content"]


def test_substring_fallback_token_overlap_without_fts(tmp_path, monkeypatch):
    store = _store_with(tmp_path, [
        ("Caroline 参加了一次支持团体活动", 0.9),
        ("完全无关的记录", 0.5),
    ])
    provider = DeerMemProvider(store=store, async_extract=False)
    # 强制走 fallback：token 重叠仍应命中中文短语。
    monkeypatch.setattr(provider, "_fts_search", lambda q, k, facts=None: None)
    result = provider.search("支持团体", top_k=5)
    assert result["count"] == 1
    assert result["results"][0]["content"] == "Caroline 参加了一次支持团体活动"


def test_search_tokens_cjk_fallback_bigrams(monkeypatch):
    import core.memory_provider as mp

    # 强制走无依赖 fallback（不用 jieba），验证 unigram + 严格相邻 bigram。
    monkeypatch.setattr(mp, "_JIEBA_AVAILABLE", False)
    tokens = mp._search_tokens("支持团体")
    assert "支" in tokens and "团" in tokens
    assert "支持" in tokens and "团体" in tokens


def test_search_tokens_jieba_words_when_available():
    import core.memory_provider as mp

    tokens = mp._search_tokens("参加支持团体")
    # 无论 jieba 是否可用，中文都应被切成多个有用 token（不再是整串一个 token）。
    assert len(tokens) >= 2
    if mp._JIEBA_AVAILABLE:
        # jieba 词级分词：应含"支持"/"团体"这类词。
        assert any(t in ("支持", "团体", "参加") for t in tokens)


def test_search_tokens_mixed_ascii_cjk(monkeypatch):
    import core.memory_provider as mp

    # 无依赖 fallback：ASCII 词小写、CJK bigram。
    monkeypatch.setattr(mp, "_JIEBA_AVAILABLE", False)
    tokens = mp._search_tokens("Python 开发 2022")
    assert "python" in tokens  # ASCII 词小写
    assert "2022" in tokens
    assert "开发" in tokens  # CJK bigram



def test_search_falls_back_when_fts_returns_none(tmp_path, monkeypatch):
    store = _store_with(tmp_path, [("fallback 内容 keyword", 0.9)])
    provider = DeerMemProvider(store=store, async_extract=False)
    # 强制 FTS 返回 None（模拟不可用）
    monkeypatch.setattr(provider, "_fts_search", lambda q, k, facts=None: None)
    result = provider.search("keyword", top_k=5)
    assert result["count"] == 1
    assert "keyword" in result["results"][0]["content"]


def test_search_deduplicates_legacy_durable_and_session_copy(tmp_path):
    store = _store_with(tmp_path, [("用户偏好中文回复", 0.9)])
    provider = DeerMemProvider(
        store=store,
        async_extract=False,
        memory_dir=str(tmp_path),
    )
    session_store = provider.set_session("conv-1")
    session_store.write_all([
        session_store.make_fact(
            "用户偏好中文回复",
            confidence=0.9,
            scope="user",
            durability="transient",
            authority="descriptive",
            metadata={
                "source_turn_ids": ["D1:2"],
                "primary_turn_id": "D1:2",
                "dia_id": "D1:2",
            },
        ),
    ])

    result = provider.search("中文回复", top_k=5, thread_id="conv-1")
    assert result["count"] == 1
    assert result["results"][0]["metadata"]["source_turn_ids"] == ["D1:2"]
    assert result["results"][0]["scope"] == "user"
    assert result["results"][0]["durability"] == "transient"


def test_memory_search_tool_dispatches_to_deermem(tmp_path, monkeypatch):
    from tools import memory_read_tool
    import core.memory_provider as mp

    store = _store_with(tmp_path, [("用户偏好中文回复", 0.9)])
    provider = DeerMemProvider(store=store, async_extract=False)
    monkeypatch.setenv("MEMORY_PROVIDER", "deermem")
    monkeypatch.setattr(mp, "get_memory_provider", lambda name=None: provider)

    out = json.loads(memory_read_tool.memory_search(query="中文", max_results=5))
    assert out["success"] is True
    assert out["provider"] == "deermem"
    assert out["count"] == 1
    assert out["results"][0]["content"] == "用户偏好中文回复"


def test_memory_search_tool_default_file_backend_unchanged(monkeypatch, tmp_path):
    """默认 file backend 时 memory_search 走原有 search_memory，不含 provider=deermem 字段。"""
    from core.memory import MemoryManager
    from tools import memory_read_tool

    monkeypatch.delenv("MEMORY_PROVIDER", raising=False)
    store = MemoryManager(memory_dir=str(tmp_path))
    store.append_memory("user", "用户喜欢简洁回答")
    monkeypatch.setattr(memory_read_tool, "memory_manager", store)

    out = json.loads(memory_read_tool.memory_search(query="简洁", target="all"))
    assert out["success"] is True
    assert "provider" not in out  # file 路径不带 provider 字段
    assert out["count"] == 1
