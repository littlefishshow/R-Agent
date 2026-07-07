from pathlib import Path

from core import sandbox_cleanup
from core.agent import RAgent


def test_cleanup_deletes_old_nested_files_and_empty_dirs_but_keeps_fresh_children(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    old_file = sandbox / "old.txt"
    old_file.write_text("old", encoding="utf-8")
    nested = sandbox / "nested"
    nested.mkdir()
    old_nested_file = nested / "old_nested.txt"
    old_nested_file.write_text("old nested", encoding="utf-8")
    fresh_nested_file = nested / "fresh_nested.txt"
    fresh_nested_file.write_text("fresh nested", encoding="utf-8")
    empty_old_dir = sandbox / "empty_old_dir"
    empty_old_dir.mkdir()
    fresh_file = sandbox / "fresh.txt"
    fresh_file.write_text("fresh", encoding="utf-8")

    now = 1_000_000.0
    original_created = sandbox_cleanup._entry_created_timestamp
    created = {
        old_file: now - 4 * 24 * 60 * 60,
        nested: now - 4 * 24 * 60 * 60,
        old_nested_file: now - 4 * 24 * 60 * 60,
        fresh_nested_file: now - 1 * 24 * 60 * 60,
        empty_old_dir: now - 4 * 24 * 60 * 60,
        fresh_file: now - 1 * 24 * 60 * 60,
    }
    sandbox_cleanup._entry_created_timestamp = lambda path: created[Path(path)]
    try:
        result = sandbox_cleanup.cleanup_sandbox_by_creation_time(
            sandbox_dir=sandbox,
            retention_days=3,
            now=now,
        )
    finally:
        sandbox_cleanup._entry_created_timestamp = original_created

    assert sandbox.exists()
    assert not old_file.exists()
    assert not old_nested_file.exists()
    assert not empty_old_dir.exists()
    assert fresh_file.exists()
    assert fresh_nested_file.exists()
    assert nested.exists(), "old parent directory with a fresh child must not be removed recursively"
    assert set(result["deleted"]) == {str(old_file), str(old_nested_file), str(empty_old_dir)}
    assert result["errors"] == []


def test_cleanup_uses_st_birthtime_when_available_and_falls_back_to_st_ctime(monkeypatch):
    calls = []

    class WithBirth:
        st_birthtime = 123.0
        st_ctime = 456.0

    class WithoutBirth:
        st_ctime = 789.0

    stat_results = [WithBirth(), WithoutBirth()]

    def fake_stat(path, *, follow_symlinks=True):
        calls.append((path, follow_symlinks))
        return stat_results.pop(0)

    monkeypatch.setattr(sandbox_cleanup.os, "stat", fake_stat)

    first = Path("first")
    second = Path("second")
    assert sandbox_cleanup._entry_created_timestamp(first) == 123.0
    assert sandbox_cleanup._entry_created_timestamp(second) == 789.0
    assert calls == [(first, False), (second, False)]


def test_cleanup_with_real_pathlib_path_deletes_old_file(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    old_file = sandbox / "old.txt"
    old_file.write_text("old", encoding="utf-8")

    created_at = sandbox_cleanup._entry_created_timestamp(old_file)
    result = sandbox_cleanup.cleanup_sandbox_by_creation_time(
        sandbox_dir=sandbox,
        retention_days=0,
        now=created_at + 1,
    )

    assert sandbox.exists()
    assert not old_file.exists()
    assert str(old_file) in result["deleted"]
    assert result["errors"] == []


def test_maybe_cleanup_respects_interval_and_disable_env(monkeypatch):
    calls = []
    monkeypatch.setattr(sandbox_cleanup, "_LAST_CLEANUP_AT", None)
    monkeypatch.setattr(sandbox_cleanup, "cleanup_sandbox_by_creation_time", lambda now=None: calls.append(now) or {"deleted": []})
    monkeypatch.setenv("R_AGENT_SANDBOX_CLEANUP_INTERVAL_SECONDS", "100")
    monkeypatch.delenv("R_AGENT_SANDBOX_CLEANUP_DISABLED", raising=False)

    assert sandbox_cleanup.maybe_cleanup_sandbox(now=1000) == {"deleted": []}
    skipped = sandbox_cleanup.maybe_cleanup_sandbox(now=1050)
    assert skipped["skipped"] == "cleanup interval has not elapsed"
    assert calls == [1000.0]

    monkeypatch.setenv("R_AGENT_SANDBOX_CLEANUP_DISABLED", "1")
    assert sandbox_cleanup.maybe_cleanup_sandbox(force=True, now=1100)["skipped"] == "sandbox cleanup disabled"
    assert calls == [1000.0]


def test_ragent_constructor_triggers_opportunistic_sandbox_cleanup(monkeypatch):
    calls = []
    monkeypatch.setattr("core.agent.maybe_cleanup_sandbox", lambda: calls.append(True) or {"deleted": []})
    monkeypatch.setattr("core.config.create_llm_client", lambda: object())

    RAgent(model="test", max_iterations=1, enable_self_review=False)

    assert calls == [True]
