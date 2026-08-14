"""Phase 2 · 准入闸门 + 容量治理测试（直接测 DeerMemProvider._apply_updates）。

覆盖：scope gate（task/imperative/transient/missing 被拒）、低置信度被拒、
max_facts 保留高置信度、factsToRemove 的 scope/reason gate、正常 fact 落盘。
"""

from core.memory_facts import FactStore
from core.memory_provider import DeerMemProvider


def _provider(tmp_path, **extractor_kwargs):
    store = FactStore(memory_dir=str(tmp_path))
    return DeerMemProvider(store=store, extractor=None, async_extract=False), store


def _fact(content, *, scope="user", durability="durable", authority="descriptive",
          confidence=0.9, category="preference", **extra):
    f = {
        "content": content, "category": category, "confidence": confidence,
        "scope": scope, "durability": durability, "authority": authority,
    }
    f.update(extra)
    return f


def test_user_durable_descriptive_fact_is_stored(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", raising=False)
    provider, store = _provider(tmp_path)
    update = {"user": {}, "history": {}, "newFacts": [_fact("用户偏好中文回复")], "factsToRemove": []}
    stats = provider._apply_updates(update, "t1")
    assert stats["added"] == 1
    assert store.load_facts()[0]["content"] == "用户偏好中文回复"


def test_task_scope_fact_rejected(tmp_path):
    provider, store = _provider(tmp_path)
    update = {"user": {}, "history": {},
              "newFacts": [_fact("这个 bug 修好了", scope="task")], "factsToRemove": []}
    stats = provider._apply_updates(update, "t1")
    assert stats["added"] == 0
    assert stats["rejected"] == 1
    assert store.count() == 0


def test_imperative_authority_rejected(tmp_path):
    provider, store = _provider(tmp_path)
    update = {"user": {}, "history": {},
              "newFacts": [_fact("先读这个文件", authority="imperative")], "factsToRemove": []}
    stats = provider._apply_updates(update, "t1")
    assert stats["rejected"] == 1
    assert store.count() == 0


def test_transient_durability_rejected(tmp_path):
    provider, store = _provider(tmp_path)
    update = {"user": {}, "history": {},
              "newFacts": [_fact("临时开个调试开关", durability="transient")], "factsToRemove": []}
    stats = provider._apply_updates(update, "t1")
    assert stats["rejected"] == 1
    assert store.count() == 0


def test_missing_classification_rejected(tmp_path):
    provider, store = _provider(tmp_path)
    bad = {"content": "缺分类", "category": "preference", "confidence": 0.9}  # 无 scope/durability/authority
    update = {"user": {}, "history": {}, "newFacts": [bad], "factsToRemove": []}
    stats = provider._apply_updates(update, "t1")
    assert stats["rejected"] == 1
    assert store.count() == 0


def test_low_confidence_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.5")
    provider, store = _provider(tmp_path)
    update = {"user": {}, "history": {},
              "newFacts": [_fact("弱置信度事实", confidence=0.3)], "factsToRemove": []}
    stats = provider._apply_updates(update, "t1")
    assert stats["rejected"] == 1
    assert store.count() == 0


def test_max_facts_keeps_highest_confidence(tmp_path, monkeypatch):
    # config 层对 MEMORY_MAX_FACTS 有 max(10, n) 下限，故用 10 + 12 条 fact 验证淘汰。
    monkeypatch.setenv("MEMORY_MAX_FACTS", "10")
    monkeypatch.setenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.0")
    provider, store = _provider(tmp_path)
    # 12 条，置信度 0.01..0.12（升序），淘汰后应保留最高的 10 条。
    new_facts = [_fact(f"fact {i:02d}", confidence=round(0.01 * i, 2)) for i in range(1, 13)]
    update = {"user": {}, "history": {}, "newFacts": new_facts, "factsToRemove": []}
    provider._apply_updates(update, "t1")
    facts = store.load_facts()
    assert len(facts) == 10
    contents = {f["content"] for f in facts}
    # 最低的两条（0.01 / 0.02）被淘汰
    assert "fact 01" not in contents
    assert "fact 02" not in contents
    assert "fact 12" in contents


def test_dedup_across_apply(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.0")
    provider, store = _provider(tmp_path)
    provider._apply_updates(
        {"user": {}, "history": {}, "newFacts": [_fact("用户在用 macOS")], "factsToRemove": []}, "t1")
    provider._apply_updates(
        {"user": {}, "history": {}, "newFacts": [_fact("用户在用 macOS")], "factsToRemove": []}, "t2")
    assert store.count() == 1


def test_contradiction_removal_requires_scope_and_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.0")
    provider, store = _provider(tmp_path)
    # 先写一条 fact
    provider._apply_updates(
        {"user": {}, "history": {}, "newFacts": [_fact("用户偏好深色模式")], "factsToRemove": []}, "t1")
    fid = store.load_facts()[0]["id"]

    # 无 reason 的删除被拒
    provider._apply_updates(
        {"user": {}, "history": {}, "newFacts": [], "factsToRemove": [{"id": fid, "scope": "user"}]}, "t2")
    assert store.count() == 1

    # 非 user scope 的删除被拒
    provider._apply_updates(
        {"user": {}, "history": {}, "newFacts": [],
         "factsToRemove": [{"id": fid, "scope": "task", "reason": "过时"}]}, "t3")
    assert store.count() == 1

    # 合法删除生效
    stats = provider._apply_updates(
        {"user": {}, "history": {}, "newFacts": [],
         "factsToRemove": [{"id": fid, "scope": "user", "reason": "用户改用浅色"}]}, "t4")
    assert stats["removed"] == 1
    assert store.count() == 0


def test_expected_valid_days_capped_at_creation(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.0")
    monkeypatch.setenv("MEMORY_STALENESS_AGE_DAYS", "90")
    monkeypatch.setenv("MEMORY_STALENESS_MAX_LIFETIME_MULTIPLIER", "20.0")  # cap = 1800
    provider, store = _provider(tmp_path)
    update = {"user": {}, "history": {},
              "newFacts": [_fact("超长寿命", expected_valid_days=100000)], "factsToRemove": []}
    provider._apply_updates(update, "t1")
    assert store.load_facts()[0]["expected_valid_days"] == 1800
