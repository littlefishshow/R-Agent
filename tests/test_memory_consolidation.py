"""Phase 5 · 自动治理测试：consolidation（默认关）。

覆盖：合并后置信度取 min 不膨胀、保护类别不参与合并、createdAt 取最早、
max_groups/max_sources 上限、默认关闭时不合并。
"""

from datetime import datetime, timedelta, timezone

from core.memory_facts import FactStore
from core.memory_provider import DeerMemProvider


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed(tmp_path, facts):
    store = FactStore(memory_dir=str(tmp_path))
    store.write_all(facts)
    return store


def _fact(fid, content, *, category="context", confidence=0.8, days_ago=10, evd=None):
    f = {
        "id": fid, "content": content, "category": category, "confidence": confidence,
        "scope": "user", "durability": "durable", "authority": "descriptive",
        "created_at": _iso(days_ago),
    }
    if evd is not None:
        f["expected_valid_days"] = evd
    return f


def test_consolidation_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORY_CONSOLIDATION_ENABLED", raising=False)
    monkeypatch.setenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.0")
    store = _seed(tmp_path, [_fact("a", "事实A"), _fact("b", "事实B")])
    provider = DeerMemProvider(store=store, async_extract=False)
    update = {"user": {}, "history": {}, "newFacts": [],
              "factsToConsolidate": [{"sourceIds": ["a", "b"],
                                      "consolidated": {"content": "合并A+B", "confidence": 0.99}}]}
    provider._apply_updates(update, "t1")
    # 默认关闭 -> 不合并，原 2 条仍在。
    assert store.count() == 2


def test_consolidation_merges_and_caps_confidence(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_CONSOLIDATION_ENABLED", "1")
    monkeypatch.setenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.0")
    store = _seed(tmp_path, [
        _fact("a", "用户喜欢咖啡", confidence=0.7, days_ago=30),
        _fact("b", "用户喜欢加奶", confidence=0.6, days_ago=10),
    ])
    provider = DeerMemProvider(store=store, async_extract=False)
    update = {"user": {}, "history": {}, "newFacts": [],
              "factsToConsolidate": [{
                  "sourceIds": ["a", "b"],
                  "consolidated": {"content": "用户喜欢加奶的咖啡", "confidence": 0.99,
                                   "category": "preference"},
              }]}
    provider._apply_updates(update, "t1")
    facts = store.load_facts()
    contents = {f["content"] for f in facts}
    assert "用户喜欢加奶的咖啡" in contents
    assert "用户喜欢咖啡" not in contents  # 源被移除
    merged = next(f for f in facts if f["content"] == "用户喜欢加奶的咖啡")
    # 置信度取 min(LLM 0.99, 源最大 0.7) = 0.7，不膨胀
    assert merged["confidence"] == 0.7


def test_consolidation_earliest_created_at(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_CONSOLIDATION_ENABLED", "1")
    monkeypatch.setenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.0")
    early = _iso(100)
    late = _iso(5)
    store = _seed(tmp_path, [
        {"id": "a", "content": "早", "category": "context", "confidence": 0.8,
         "scope": "user", "durability": "durable", "authority": "descriptive", "created_at": early},
        {"id": "b", "content": "晚", "category": "context", "confidence": 0.8,
         "scope": "user", "durability": "durable", "authority": "descriptive", "created_at": late},
    ])
    provider = DeerMemProvider(store=store, async_extract=False)
    update = {"user": {}, "history": {}, "newFacts": [],
              "factsToConsolidate": [{"sourceIds": ["a", "b"],
                                      "consolidated": {"content": "早晚合并", "confidence": 0.8}}]}
    provider._apply_updates(update, "t1")
    merged = next(f for f in store.load_facts() if f["content"] == "早晚合并")
    assert merged["created_at"] == early  # 取最早


def test_consolidation_protected_category_not_merged(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_CONSOLIDATION_ENABLED", "1")
    monkeypatch.setenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.0")
    store = _seed(tmp_path, [
        _fact("a", "普通事实"),
        _fact("c", "重要纠正", category="correction"),
    ])
    provider = DeerMemProvider(store=store, async_extract=False)
    update = {"user": {}, "history": {}, "newFacts": [],
              "factsToConsolidate": [{"sourceIds": ["a", "c"],
                                      "consolidated": {"content": "不该合并", "confidence": 0.8}}]}
    provider._apply_updates(update, "t1")
    contents = {f["content"] for f in store.load_facts()}
    # 含保护类别的组不合并，原样保留。
    assert contents == {"普通事实", "重要纠正"}


def test_consolidation_max_groups_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_CONSOLIDATION_ENABLED", "1")
    monkeypatch.setenv("MEMORY_CONSOLIDATION_MAX_GROUPS_PER_CYCLE", "1")
    monkeypatch.setenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.0")
    store = _seed(tmp_path, [_fact(f"f{i}", f"事实{i}") for i in range(4)])
    provider = DeerMemProvider(store=store, async_extract=False)
    update = {"user": {}, "history": {}, "newFacts": [],
              "factsToConsolidate": [
                  {"sourceIds": ["f0", "f1"], "consolidated": {"content": "组1", "confidence": 0.8}},
                  {"sourceIds": ["f2", "f3"], "consolidated": {"content": "组2", "confidence": 0.8}},
              ]}
    provider._apply_updates(update, "t1")
    contents = {f["content"] for f in store.load_facts()}
    # 只合并 1 组：组1 生效，组2 未合并（f2/f3 仍在）。
    assert "组1" in contents
    assert "组2" not in contents
    assert "事实2" in contents and "事实3" in contents


def test_consolidation_min_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_CONSOLIDATION_ENABLED", "1")
    monkeypatch.setenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.0")
    store = _seed(tmp_path, [_fact("a", "单独事实")])
    provider = DeerMemProvider(store=store, async_extract=False)
    # 只有 1 个源 -> 不合并
    update = {"user": {}, "history": {}, "newFacts": [],
              "factsToConsolidate": [{"sourceIds": ["a"],
                                      "consolidated": {"content": "无效合并", "confidence": 0.8}}]}
    provider._apply_updates(update, "t1")
    assert {f["content"] for f in store.load_facts()} == {"单独事实"}
