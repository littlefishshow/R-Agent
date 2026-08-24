"""Phase 5 · 自动治理测试：staleness review（默认关）。

覆盖：过期候选删除有 per-cycle cap、保护类别不被删、LLM 乱报 id 被拒、续期不超上限、
默认关闭时不治理。
"""

from datetime import datetime, timedelta, timezone

from core.memory_facts import FactStore
from core.memory_provider import DeerMemProvider


def _old_iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed(tmp_path, facts):
    store = FactStore(memory_dir=str(tmp_path))
    store.write_all(facts)
    return store


def _fact(fid, content, *, category="context", confidence=0.8, days_ago=200, evd=None):
    f = {
        "id": fid, "content": content, "category": category, "confidence": confidence,
        "scope": "user", "durability": "durable", "authority": "descriptive",
        "created_at": _old_iso(days_ago),
    }
    if evd is not None:
        f["expected_valid_days"] = evd
    return f


def test_staleness_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORY_STALENESS_ENABLED", raising=False)
    monkeypatch.setenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.0")
    store = _seed(tmp_path, [_fact("f1", "老事实", days_ago=500)])
    provider = DeerMemProvider(store=store, async_extract=False)
    update = {"user": {}, "history": {}, "newFacts": [],
              "staleFactsToRemove": [{"id": "f1", "reason": "过时"}]}
    provider._apply_updates(update, "t1")
    # 默认关闭 -> 不治理，fact 仍在。
    assert store.count() == 1


def test_staleness_removes_only_real_candidates(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STALENESS_ENABLED", "1")
    monkeypatch.setenv("MEMORY_STALENESS_AGE_DAYS", "90")
    monkeypatch.setenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.0")
    store = _seed(tmp_path, [
        _fact("old1", "过期事实", days_ago=200),
        _fact("fresh1", "新鲜事实", days_ago=5),
    ])
    provider = DeerMemProvider(store=store, async_extract=False)
    # LLM 提议删除 old1（真候选）与 fresh1（非候选，应被拒）+ 乱报 ghost
    update = {"user": {}, "history": {}, "newFacts": [],
              "staleFactsToRemove": [
                  {"id": "old1", "reason": "过期"},
                  {"id": "fresh1", "reason": "乱报"},
                  {"id": "ghost", "reason": "不存在"},
              ]}
    provider._apply_updates(update, "t1")
    ids = {f["id"] for f in store.load_facts()}
    assert "old1" not in ids       # 真候选被删
    assert "fresh1" in ids         # 非候选不被删


def test_staleness_protected_category_not_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STALENESS_ENABLED", "1")
    monkeypatch.setenv("MEMORY_STALENESS_AGE_DAYS", "90")
    monkeypatch.setenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.0")
    store = _seed(tmp_path, [_fact("c1", "重要纠正", category="correction", days_ago=500)])
    provider = DeerMemProvider(store=store, async_extract=False)
    update = {"user": {}, "history": {}, "newFacts": [],
              "staleFactsToRemove": [{"id": "c1", "reason": "过期"}]}
    provider._apply_updates(update, "t1")
    # correction 是保护类别，永不成为候选 -> 不删。
    assert {f["id"] for f in store.load_facts()} == {"c1"}


def test_staleness_per_cycle_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STALENESS_ENABLED", "1")
    monkeypatch.setenv("MEMORY_STALENESS_AGE_DAYS", "90")
    monkeypatch.setenv("MEMORY_STALENESS_MAX_REMOVALS_PER_CYCLE", "2")
    monkeypatch.setenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.0")
    facts = [_fact(f"o{i}", f"过期 {i}", days_ago=200, confidence=round(0.1 * i, 2)) for i in range(5)]
    store = _seed(tmp_path, facts)
    provider = DeerMemProvider(store=store, async_extract=False)
    update = {"user": {}, "history": {}, "newFacts": [],
              "staleFactsToRemove": [{"id": f"o{i}", "reason": "过期"} for i in range(5)]}
    provider._apply_updates(update, "t1")
    remaining = store.load_facts()
    # 5 个候选，cap=2 -> 只删 2 个，剩 3 个；优先删最低置信度。
    assert len(remaining) == 3
    removed_ids = {"o0", "o1"}  # 置信度 0.0 / 0.1 最低
    assert removed_ids.isdisjoint({f["id"] for f in remaining})


def test_staleness_extension_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STALENESS_ENABLED", "1")
    monkeypatch.setenv("MEMORY_STALENESS_AGE_DAYS", "90")
    monkeypatch.setenv("MEMORY_STALENESS_MAX_EXTENSION_DAYS", "3650")
    monkeypatch.setenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.0")
    store = _seed(tmp_path, [_fact("e1", "可续期事实", days_ago=200)])
    provider = DeerMemProvider(store=store, async_extract=False)
    update = {"user": {}, "history": {}, "newFacts": [],
              "staleFactsToExtend": [{"id": "e1", "extend_by_days": 999999}]}
    provider._apply_updates(update, "t1")
    fact = store.load_facts()[0]
    assert fact["expected_valid_days"] <= 3650


def test_staleness_proposed_removal_not_extended(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STALENESS_ENABLED", "1")
    monkeypatch.setenv("MEMORY_STALENESS_AGE_DAYS", "90")
    monkeypatch.setenv("MEMORY_STALENESS_MAX_REMOVALS_PER_CYCLE", "0" if False else "10")
    monkeypatch.setenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.0")
    store = _seed(tmp_path, [_fact("x1", "同时提议删和续", days_ago=200)])
    provider = DeerMemProvider(store=store, async_extract=False)
    update = {"user": {}, "history": {}, "newFacts": [],
              "staleFactsToRemove": [{"id": "x1", "reason": "删"}],
              "staleFactsToExtend": [{"id": "x1", "extend_by_days": 100}]}
    provider._apply_updates(update, "t1")
    # 被提议删除的 fact 直接删掉，不会被续期。
    assert store.count() == 0


def test_governance_due_requires_three_days_and_new_fact(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STALENESS_ENABLED", "1")
    monkeypatch.setenv("MEMORY_GOVERNANCE_INTERVAL_DAYS", "3")
    store = _seed(tmp_path, [_fact("f1", "事实1", days_ago=10)])
    provider = DeerMemProvider(
        store=store,
        async_extract=False,
        memory_dir=str(tmp_path),
    )
    day0 = datetime(2026, 8, 1, tzinfo=timezone.utc)

    # 首次只建立基线。
    assert provider._governance_due(store.load_facts(), now=day0) is False

    # 未满三天，即使新增 fact 也不整理。
    store.append_fact(_fact("f2", "事实2", days_ago=1))
    assert provider._governance_due(
        store.load_facts(),
        now=day0 + timedelta(days=2),
    ) is False

    # 满三天 + 有新增，才允许整理。
    assert provider._governance_due(
        store.load_facts(),
        now=day0 + timedelta(days=3),
    ) is True
    provider._mark_governance_run(
        store.load_facts(),
        now=day0 + timedelta(days=3),
    )

    # 之后即使再过三天，但没有新增 fact，也不整理。
    assert provider._governance_due(
        store.load_facts(),
        now=day0 + timedelta(days=6),
    ) is False

    # 再有新增 fact 后恢复可整理。
    store.append_fact(_fact("f3", "事实3", days_ago=1))
    assert provider._governance_due(
        store.load_facts(),
        now=day0 + timedelta(days=6),
    ) is True
