import json
from pathlib import Path

from autoresearch.autoresearch_tool import auto_research_run_v2_tool
from autoresearch.autoresearch_memory import read_phase, split_program


def test_auto_research_run_v2_end_to_end_deterministic(tmp_path):
    (tmp_path / "program.md").write_text("Goal: maximize accuracy under 100ms\n", encoding="utf-8")
    (tmp_path / "train.py").write_text("def train():\n    return 0\n", encoding="utf-8")
    (tmp_path / "eval.sh").write_text("#!/usr/bin/env bash\necho 'primary_metric: 0.5'\n", encoding="utf-8")

    payload = json.loads(auto_research_run_v2_tool(
        str(tmp_path),
        project_id="v2e2e",
        max_steps=8,
        use_llm_step_agents=False,   # deterministic handlers
        use_git_versioning=False,
        background=False,            # test the synchronous path directly
    ))

    assert payload["success"] is True
    assert "preflight" in payload
    # program.md was scaffolded into L0/L1
    prog = (tmp_path / "program.md").read_text(encoding="utf-8")
    assert split_program(prog).has_markers is True
    # project.md created with a valid phase marker
    project = (tmp_path / "project.md").read_text(encoding="utf-8")
    phase, _ = read_phase(project)
    assert phase in {"plan", "attempt", "conclude", "pause"}
    # survey ran
    assert (tmp_path / ".auto" / "survey.md").exists()
    # budget ledger exists
    assert (tmp_path / ".autoresearch" / "budget.json").exists()
    # steps were recorded; init survey is run before the first public 3-step phase.
    assert payload["steps"][0]["ran_phase"] == "plan"


def test_auto_research_run_v2_pauses_on_budget(tmp_path):
    (tmp_path / "program.md").write_text("Goal: x\n", encoding="utf-8")
    payload = json.loads(auto_research_run_v2_tool(
        str(tmp_path),
        project_id="v2budget",
        max_steps=20,
        max_tokens=1,          # effectively force degrade/exhaust paths quickly
        use_llm_step_agents=False,
        use_git_versioning=False,
        background=False,      # test the synchronous path directly
    ))
    assert payload["success"] is True
    assert "budget" in payload
    # ledger present and reflects the configured limit
    ledger = json.loads((tmp_path / ".autoresearch" / "budget.json").read_text(encoding="utf-8"))
    assert ledger["limits"]["max_tokens"] == 1
