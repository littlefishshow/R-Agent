import json
from pathlib import Path

from core.autoresearch_loop import AutoResearchSettings, AutoResearchLoop
from core.autoresearch_phases import (
    PhaseSignals,
    phase_gate,
    budget_gate,
    next_phase,
    PhaseController,
    PhaseResult,
)
from core.autoresearch_phase_handlers import default_handlers, survey_project
from core.autoresearch_memory import read_phase, split_program
from core.autoresearch_gate_state import save_gate_state


# --------------------------------------------------------------------------- #
# Pure transitions
# --------------------------------------------------------------------------- #

def test_phase_gate_branches():
    assert phase_gate(PhaseSignals(phase="gate", started=True))[0] == "plan"
    assert phase_gate(PhaseSignals(phase="gate", started=False, pareto_changed=True))[0] == "plan"
    assert phase_gate(PhaseSignals(phase="gate", started=False, plateau_counter=3, plateau_patience=3))[0] == "plan"
    assert phase_gate(PhaseSignals(phase="gate", started=False, plan_still_valid=False))[0] == "plan"
    assert phase_gate(PhaseSignals(phase="gate", started=False, plan_still_valid=True))[0] == "execute"


def test_budget_gate_pauses_on_exhaustion_and_plateau():
    assert budget_gate(PhaseSignals(phase="compress", budget_exhausted=True))[0] == "pause"
    assert budget_gate(PhaseSignals(phase="compress", plateau_counter=5, plateau_patience=3, pareto_changed=False))[0] == "pause"
    assert budget_gate(PhaseSignals(phase="compress", plateau_counter=0))[0] == "gate"


def test_next_phase_linear_edges():
    assert next_phase(PhaseSignals(phase="plan"))[0] == "execute"
    assert next_phase(PhaseSignals(phase="execute"))[0] == "run"
    assert next_phase(PhaseSignals(phase="run"))[0] == "evaluate"
    assert next_phase(PhaseSignals(phase="evaluate"))[0] == "compress"


def test_next_phase_major_error_jumps_to_evaluate():
    assert next_phase(PhaseSignals(phase="execute", major_error=True))[0] == "evaluate"
    assert next_phase(PhaseSignals(phase="run", major_error=True))[0] == "evaluate"


def test_next_phase_solved_run_pauses():
    assert next_phase(PhaseSignals(phase="run", solved=True)) == ("pause", "objective solved: pause")


# --------------------------------------------------------------------------- #
# Controller: scaffolding + one full cycle
# --------------------------------------------------------------------------- #

def _make_controller(tmp_path, **overrides):
    (tmp_path / "program.md").write_text("Goal: maximize accuracy\nsuccess: acc up\n", encoding="utf-8")
    (tmp_path / "train.py").write_text("def train():\n    pass\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False, **overrides)
    loop = AutoResearchLoop(settings)
    return PhaseController(settings, handlers=default_handlers(), loop=loop)


def test_controller_scaffolds_program_and_project(tmp_path):
    ctrl = _make_controller(tmp_path)
    ctrl.ensure_scaffold()
    prog = (tmp_path / "program.md").read_text(encoding="utf-8")
    assert split_program(prog).has_markers is True
    assert (tmp_path / "project.md").exists()
    phase, _ = ctrl.current_phase()
    assert phase == "init"


def test_controller_init_step_runs_survey_and_advances_to_plan(tmp_path):
    ctrl = _make_controller(tmp_path)
    report = ctrl.step()
    assert report["ran_phase"] == "init"
    # init -> gate -> (started) plan
    assert report["next_phase"] == "plan"
    assert (tmp_path / ".auto" / "survey.md").exists()
    survey = (tmp_path / ".auto" / "survey.md").read_text(encoding="utf-8")
    assert "File tree" in survey
    phase, _ = ctrl.current_phase()
    assert phase == "plan"


def test_controller_full_cycle_reaches_gate_or_pause(tmp_path):
    ctrl = _make_controller(tmp_path)
    reports = ctrl.run(max_steps=8)
    ran = [r["ran_phase"] for r in reports]
    # First cycle should visit init, plan, execute, run, evaluate, compress
    assert ran[0] == "init"
    assert "evaluate" in ran
    assert "compress" in ran
    # compress with unlimited budget loops back through the gate into a new cycle
    assert "execute" in ran[6:] or "plan" in ran[6:]
    # the machine keeps cycling; next_phase is always a valid phase
    from core.autoresearch_memory import PHASES
    assert reports[-1]["next_phase"] in PHASES


def test_controller_survey_excludes_git_and_autoresearch(tmp_path):
    (tmp_path / ".autoresearch").mkdir()
    (tmp_path / ".autoresearch" / "secret.json").write_text("{}", encoding="utf-8")
    (tmp_path / "real_code.py").write_text("x = 1\n", encoding="utf-8")
    text = survey_project(tmp_path)
    assert "real_code.py" in text
    assert "secret.json" not in text


def test_controller_budget_exhaustion_pauses(tmp_path):
    ctrl = _make_controller(tmp_path, max_tokens=1)
    # force the ledger past its limit
    ctrl.loop.budget.record(prompt_tokens=100, completion_tokens=100, model="gpt-4o", phase="plan")
    assert ctrl.loop.budget.is_exhausted() is True
    reports = ctrl.run(max_steps=10)
    # run() stops once budget is exhausted
    assert len(reports) >= 1


def test_controller_build_signals_reads_gate_state(tmp_path):
    ctrl = _make_controller(tmp_path)
    save_gate_state(tmp_path, {
        "pareto_changed": True,
        "plateau_counter": 2,
        "plan_still_valid": False,
    })
    sig = ctrl.build_signals("gate")
    assert sig.pareto_changed is True
    assert sig.plateau_counter == 2
    assert sig.plan_still_valid is False


def test_evaluate_handler_writes_lesson_on_major_error(tmp_path):
    ctrl = _make_controller(tmp_path)
    ctrl.ensure_scaffold()
    # jump project.md to evaluate phase and step with a major_error signal
    from core.autoresearch_memory import write_phase
    ctrl._atomic_write(ctrl._project_path(), write_phase(ctrl.read_project(), "evaluate", "forced"))
    report = ctrl.step(extra_signals={"major_error": True})
    assert report["ran_phase"] == "evaluate"
    lessons = (tmp_path / ".autoresearch" / "lessons.jsonl")
    assert lessons.exists()
    rows = [json.loads(l) for l in lessons.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows and rows[-1]["kind"] in {"operational_error", "directional_error"}


def test_compress_handler_trims_oversized_belief(tmp_path):
    from core.autoresearch_memory import update_belief
    from core.autoresearch_phase_handlers import make_compress_handler
    from core.autoresearch_phases import PhaseContext, PhaseSignals

    prog = (tmp_path / "program.md")
    prog.write_text("Goal: X\n", encoding="utf-8")
    from core.autoresearch_memory import ensure_program_scaffold
    scaffolded = ensure_program_scaffold(prog.read_text(encoding="utf-8"))
    big_belief = "B" * 9000
    program_text = update_belief(scaffolded, big_belief)
    handler = make_compress_handler(max_belief_chars=1000)
    ctx = PhaseContext(phase="compress", root=tmp_path, program_text=program_text,
                       project_text="# Project\n", signals=PhaseSignals(phase="compress"))
    result = handler(ctx)
    assert result.program_text is not None
    assert len(split_program(result.program_text).belief) <= 1000


def test_v2_controller_stop_sentinel_halts_run(tmp_path):
    (tmp_path / "program.md").write_text("Goal: x\n", encoding="utf-8")
    (tmp_path / ".autoresearch").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".autoresearch" / "STOP").write_text("stop\n", encoding="utf-8")
    ctrl = _make_controller(tmp_path)
    reports = ctrl.run(max_steps=50)
    # STOP present from the start => no phase steps executed
    assert reports == []
