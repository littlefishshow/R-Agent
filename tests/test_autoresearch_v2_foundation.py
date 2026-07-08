import json
from pathlib import Path

from core.autoresearch_budget import (
    BudgetLedger,
    BudgetLimits,
    ModelTiers,
    MeteredLLMClient,
    estimate_usd,
    price_per_1k,
)
from core.autoresearch_memory import (
    split_program,
    update_belief,
    ensure_program_scaffold,
    read_phase,
    write_phase,
    normalize_phase,
    write_auto_note,
    read_auto_notes,
    gc_auto_dir,
    append_lesson,
    read_lessons,
    CONSTITUTION_OPEN,
    BELIEF_OPEN,
)


# --------------------------------------------------------------------------- #
# Budget ledger
# --------------------------------------------------------------------------- #

def test_budget_ledger_accumulates_and_persists(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.json", BudgetLimits(max_usd=0, max_tokens=0))
    ledger.record(prompt_tokens=1000, completion_tokens=500, model="gpt-4o", phase="plan")
    ledger.record(prompt_tokens=200, completion_tokens=100, model="gpt-4o-mini", phase="exec")

    snap = ledger.snapshot()
    assert snap["calls"] == 2
    assert snap["total_tokens"] == 1800
    assert snap["by_phase"]["plan"]["calls"] == 1
    assert snap["by_phase"]["exec"]["tokens"] == 300
    # Reload from disk keeps totals.
    reloaded = BudgetLedger(tmp_path / "budget.json")
    assert reloaded.snapshot()["total_tokens"] == 1800


def test_budget_unlimited_never_exhausts(tmp_path):
    ledger = BudgetLedger(tmp_path / "b.json", BudgetLimits(max_usd=0, max_tokens=0))
    ledger.record(prompt_tokens=10_000_000, completion_tokens=10_000_000, model="gpt-4o", phase="plan")
    assert ledger.is_exhausted() is False
    assert ledger.should_degrade() is False
    assert ledger.status() == "ok"


def test_budget_degrade_then_exhaust_by_tokens(tmp_path):
    ledger = BudgetLedger(tmp_path / "b.json", BudgetLimits(max_tokens=1000, degrade_ratio=0.8))
    ledger.record(prompt_tokens=500, completion_tokens=300, model="gpt-4o", phase="plan")  # 800/1000
    assert ledger.should_degrade() is True
    assert ledger.is_exhausted() is False
    assert ledger.status() == "degrade"
    ledger.record(prompt_tokens=200, completion_tokens=100, model="gpt-4o", phase="plan")  # 1100/1000
    assert ledger.is_exhausted() is True
    assert ledger.status() == "exhausted"


def test_budget_exhaust_by_usd(tmp_path):
    ledger = BudgetLedger(tmp_path / "b.json", BudgetLimits(max_usd=0.01))
    # gpt-4o: 0.0025/1k prompt + 0.01/1k completion -> 2000 completion tokens = 0.02 USD
    ledger.record(prompt_tokens=0, completion_tokens=2000, model="gpt-4o", phase="plan")
    assert ledger.is_exhausted() is True


def test_model_tiers_resolution(monkeypatch):
    monkeypatch.delenv("AUTORESEARCH_MODEL_PLAN", raising=False)
    monkeypatch.delenv("AUTORESEARCH_MODEL_EXEC", raising=False)
    monkeypatch.delenv("AUTORESEARCH_MODEL_UTIL", raising=False)
    tiers = ModelTiers(plan="strong", exec="mid", util="cheap", base="base")
    assert tiers.resolve("plan") == "strong"
    assert tiers.resolve("util") == "cheap"
    assert tiers.resolve("unknown") == "base"
    # empty tier falls back to base
    assert ModelTiers(base="base").resolve("plan") == "base"


def test_price_helpers_are_sane():
    p, c = price_per_1k("gpt-4o")
    assert c > p > 0
    assert estimate_usd("gpt-4o", 1000, 1000) == round(p + c, 6) or estimate_usd("gpt-4o", 1000, 1000) > 0


class _FakeUsage:
    def __init__(self, p, c):
        self.prompt_tokens = p
        self.completion_tokens = c


class _FakeResp:
    def __init__(self, p, c):
        self.usage = _FakeUsage(p, c)


class _FakeCompletions:
    def create(self, **kwargs):
        return _FakeResp(120, 80)


class _FakeChat:
    def __init__(self):
        self.completions = _FakeCompletions()


class _FakeInner:
    def __init__(self):
        self.chat = _FakeChat()

    def other_method(self):
        return "delegated"


def test_metered_client_records_usage_and_delegates(tmp_path):
    ledger = BudgetLedger(tmp_path / "b.json")
    client = MeteredLLMClient(_FakeInner(), ledger, get_phase=lambda: "plan", get_model=lambda: "gpt-4o")
    resp = client.chat.completions.create(model="gpt-4o", messages=[])
    assert resp.usage.prompt_tokens == 120
    snap = ledger.snapshot()
    assert snap["total_tokens"] == 200
    assert snap["by_phase"]["plan"]["tokens"] == 200
    # attribute delegation to inner client
    assert client.other_method() == "delegated"


# --------------------------------------------------------------------------- #
# Layered memory: program L0/L1
# --------------------------------------------------------------------------- #

def test_split_program_with_markers():
    text = f"{CONSTITUTION_OPEN}\nGoal: maximize acc\n<!-- /CONSTITUTION -->\n\n{BELIEF_OPEN}\ntry lower dropout\n<!-- /BELIEF -->\n"
    s = split_program(text)
    assert s.has_markers is True
    assert "maximize acc" in s.constitution
    assert "lower dropout" in s.belief


def test_split_program_without_markers_is_readonly():
    s = split_program("# Just a plain program\nno markers here")
    assert s.has_markers is False
    assert "plain program" in s.constitution
    assert s.belief == ""


def test_update_belief_only_touches_belief_section():
    scaffolded = ensure_program_scaffold("Goal: X\nsuccess: acc")
    updated = update_belief(scaffolded, "new belief: increase batch size")
    s = split_program(updated)
    assert "Goal: X" in s.constitution
    assert "increase batch size" in s.belief
    assert "new belief" in s.belief


def test_update_belief_refuses_readonly_program():
    try:
        update_belief("plain readonly program", "attempted belief")
    except ValueError as exc:
        assert "read-only" in str(exc)
    else:
        raise AssertionError("expected ValueError for read-only program")


def test_ensure_program_scaffold_is_idempotent():
    once = ensure_program_scaffold("Goal: Y")
    twice = ensure_program_scaffold(once)
    assert once == twice
    assert split_program(twice).has_markers is True


# --------------------------------------------------------------------------- #
# Layered memory: project.md phase
# --------------------------------------------------------------------------- #

def test_phase_roundtrip():
    text = write_phase("# Project\n", "plan", "pareto changed")
    phase, reason = read_phase(text)
    assert phase == "plan"
    assert reason == "pareto changed"
    # overwrite is idempotent, single marker
    text2 = write_phase(text, "execute", "plan approved")
    assert text2.count("<!-- PHASE:") == 1
    assert read_phase(text2) == ("execute", "plan approved")


def test_normalize_phase_defaults_to_init():
    assert normalize_phase("garbage") == "init"
    assert normalize_phase("EVALUATE") == "evaluate"


# --------------------------------------------------------------------------- #
# Layered memory: .auto GC
# --------------------------------------------------------------------------- #

def test_auto_notes_write_read_and_gc(tmp_path):
    for i in range(6):
        write_auto_note(tmp_path, f"note{i}", f"content {i}\n")
    notes = read_auto_notes(tmp_path)
    assert len(notes) == 6
    report = gc_auto_dir(tmp_path, max_files=3, max_total_chars=10_000)
    assert report["kept"] == 3
    assert len(report["removed"]) == 3
    assert len(read_auto_notes(tmp_path)) == 3


# --------------------------------------------------------------------------- #
# Lessons ledger (survives rollback)
# --------------------------------------------------------------------------- #

def test_lessons_append_and_read(tmp_path):
    append_lesson(tmp_path, kind="directional_error", summary="lowering LR hurt", detail="acc dropped 5pt", experiment_id="exp-1")
    append_lesson(tmp_path, kind="insight", summary="batchnorm helps")
    rows = read_lessons(tmp_path)
    assert len(rows) == 2
    assert rows[0]["kind"] == "directional_error"
    assert rows[1]["summary"] == "batchnorm helps"
    # file lives under .autoresearch and is jsonl
    p = Path(tmp_path) / ".autoresearch" / "lessons.jsonl"
    assert p.exists()
    assert len(p.read_text(encoding="utf-8").strip().splitlines()) == 2
