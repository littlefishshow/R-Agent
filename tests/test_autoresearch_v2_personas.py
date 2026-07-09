import json
import time
from pathlib import Path

from core.autoresearch_loop import AutoResearchSettings, AutoResearchLoop
from core.autoresearch_personas import (
    PlanDebate,
    DebateConfig,
    make_plan_handler,
    DEFAULT_PERSONAS,
    _classify_plan_item,
    _coalesce_plan_items,
    _ensure_baseline_checkpoint,
    _plan_to_todo_state,
    _planner_project_context,
    _split_inline_plan_items,
)
from core.autoresearch_phases import PhaseContext, PhaseSignals
from core.autoresearch_memory import (
    ensure_program_scaffold,
    split_program,
    read_phase,
    write_phase,
)
from core.autoresearch_todo_state import load_todo_state, save_todo_state


def _fake_chat_factory():
    """Return a chat(system,user)->json that answers as persona or leader."""
    calls = []

    def chat(system, user):
        calls.append((system, user))
        if system.startswith("You are the LEADER"):
            return json.dumps({
                "belief": "prefer smaller LR with warmup",
                "plan": "add LR warmup and re-eval",
                "detailed_plan": "1. edit train config\n2. run\n3. eval",
                "rationale": "pragmatic said feasible; divergent liked warmup",
            })
        if system.startswith("You are the DIVERGENT"):
            return json.dumps({"opinion": "try cosine warmup", "ideas": ["warmup"], "risks": ["slower"]})
        return json.dumps({"opinion": "warmup is feasible", "feasible": ["warmup"], "reject": []})

    return chat, calls


def test_debate_persona_count_respects_budget_degrade():
    chat, _ = _fake_chat_factory()
    debate = PlanDebate(chat, config=DebateConfig(max_personas=2, degrade_personas=1))
    normal = debate.run(program_text="", project_text="", degrade=False)
    degraded = debate.run(program_text="", project_text="", degrade=True)
    # normal uses 2 personas + leader; degrade uses 1 + leader
    assert len(normal["personas_used"]) == 3
    assert len(degraded["personas_used"]) == 2
    assert normal["personas_used"][-1] == "leader"


def test_debate_leader_forces_a_decision():
    chat, _ = _fake_chat_factory()
    debate = PlanDebate(chat)
    result = debate.run(program_text="", project_text="")
    assert result["belief"] == "prefer smaller LR with warmup"
    assert result["plan"] == "add LR warmup and re-eval"
    assert result["detailed_plan"].startswith("1.")


def test_debate_personas_run_in_parallel_before_leader():
    order = []

    def chat(system, user):  # noqa: ARG001
        if system.startswith("You are the LEADER"):
            order.append("leader")
            return json.dumps({"belief": "b", "plan": "p", "detailed_plan": "d"})
        time.sleep(0.2)
        if system.startswith("You are the DIVERGENT"):
            order.append("divergent")
            return json.dumps({"opinion": "wide"})
        order.append("pragmatic")
        return json.dumps({"opinion": "feasible"})

    started = time.time()
    result = PlanDebate(chat, config=DebateConfig(max_personas=2)).run(program_text="", project_text="")
    elapsed = time.time() - started
    assert elapsed < 0.35
    assert result["personas_used"] == ["divergent", "pragmatic", "leader"]
    assert order[-1] == "leader"


def test_debate_survives_persona_failure():
    def flaky_chat(system, user):
        if system.startswith("You are the DIVERGENT"):
            raise RuntimeError("boom")
        if system.startswith("You are the LEADER"):
            return json.dumps({"belief": "b", "plan": "p", "detailed_plan": "d"})
        return json.dumps({"opinion": "ok"})

    debate = PlanDebate(flaky_chat)
    result = debate.run(program_text="", project_text="")
    # leader still decided despite divergent failing
    assert result["plan"] == "p"


def test_plan_item_classification_prefers_implementation_verbs():
    assert _classify_plan_item("Create or update train/train.sh so it reliably runs a Python optimizer") == "implementation"
    assert _classify_plan_item("Add a persistent history file containing every evaluated x,y,z") == "implementation"
    assert _classify_plan_item("Run bash train/train.sh, then bash eval.sh and compare metrics") == "validation"
    assert _classify_plan_item("Run deterministic global exploration over several boxes") == "implementation"
    assert _classify_plan_item("run a broad but bounded deterministic global design") == "implementation"
    assert _classify_plan_item("Run local refinement from the best few incumbents") == "implementation"
    assert _classify_plan_item("Evaluate candidates with the oracle and update incumbent") == "implementation"
    assert _classify_plan_item("Inspect existing train structure") == "analysis"


def test_split_inline_plan_items_handles_numbered_sentence():
    text = "Inspect files. 2. Create train optimizer. 3. Run bash train/train.sh and bash eval.sh."
    assert _split_inline_plan_items(text) == [
        "Inspect files",
        "Create train optimizer",
        "Run bash train/train.sh and bash eval.sh",
    ]


def test_plan_items_coalesce_many_implementation_bullets():
    items = [
        "Inspect train files",
        "Implement optimizer",
        "Add history logging",
        "Create oracle wrapper",
        "Add restart logic",
        "Write best candidate",
        "Run bash train/train.sh and bash eval.sh",
    ]
    coalesced = _coalesce_plan_items(items, max_implementation_tasks=2)
    assert coalesced[0] == "Inspect train files"
    assert len([item for item in coalesced if _classify_plan_item(item) == "implementation"]) == 2
    assert coalesced[-1].startswith("Run bash")


def test_plan_items_can_preserve_dag_granularity_without_coalescing():
    items = [
        "Inspect train files",
        "Implement optimizer",
        "Add history logging",
        "Run bash train/train.sh and bash eval.sh",
    ]
    assert _coalesce_plan_items(items, max_implementation_tasks=0) == items


def test_planner_project_context_includes_relevant_project_files(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "project.md").write_text("Project state\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.py").write_text("print('train context marker')\n", encoding="utf-8")
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "noise.json").write_text('{"large":"runtime"}\n', encoding="utf-8")

    context = _planner_project_context(tmp_path, max_chars=5000)

    assert "train/train.py" in context
    assert "train context marker" in context
    assert "outputs/noise.json" not in context


def test_plan_to_todo_state_validation_depends_on_recent_execute_slice():
    state = _plan_to_todo_state(
        "1. Implement optimizer\n"
        "2. Run bash train/train.sh and bash eval.sh\n"
        "3. Add restart logic\n"
        "4. Run final validation\n"
    )
    tasks = state["tasks"]
    assert tasks[1]["depends_on"] == ["t1"]
    assert tasks[3]["depends_on"] == ["t3"]


def test_baseline_checkpoint_inserted_before_first_implementation():
    state = _plan_to_todo_state(
        "1. Inspect train files\n"
        "2. Implement optimizer\n"
        "3. Run bash train/train.sh and bash eval.sh\n"
    )
    state = _ensure_baseline_checkpoint(state)
    tasks = state["tasks"]
    assert [t["task_id"] for t in tasks[:3]] == ["t1", "baseline", "t2"]
    assert tasks[1]["type"] == "validation"
    assert tasks[1]["depends_on"] == ["t1"]
    assert tasks[1]["run_spec"]["commands"] == ["bash train/train.sh", "bash eval.sh"]


def test_leader_typed_tasks_preserve_explicit_dag_dependencies(tmp_path):
    chat, _ = _fake_chat_factory()

    def typed_chat(system, user):
        if system.startswith("You are the LEADER"):
            return json.dumps({
                "belief": "use dag",
                "plan": "typed dag",
                "detailed_plan": "",
                "tasks": [
                    {"task_id": "survey", "type": "analysis", "goal": "inspect files"},
                    {"task_id": "impl", "type": "implementation", "goal": "write optimizer", "depends_on": ["survey"]},
                    {"task_id": "check", "type": "validation", "goal": "run eval", "depends_on": ["impl"], "run_spec": {"commands": ["python -m json.tool outputs/submission.json", "python - <<'PY'\nprint('ok')\nPY"]}},
                    {"task_id": "polish", "type": "implementation", "goal": "improve optimizer"},
                ],
            })
        return chat(system, user)

    loop = _make_loop(tmp_path)
    program_text = ensure_program_scaffold((tmp_path / "program.md").read_text(encoding="utf-8"))
    ctx = PhaseContext(
        phase="plan",
        root=tmp_path,
        program_text=program_text,
        project_text="# Project State\n\n## 当前计划\nold\n",
        signals=PhaseSignals(phase="plan"),
        loop=loop,
    )
    make_plan_handler(typed_chat)(ctx)
    tasks = load_todo_state(tmp_path)["tasks"]
    by_id = {task["task_id"]: task for task in tasks}
    assert by_id["check"]["depends_on"] == ["impl"]
    assert by_id["check"]["run_spec"]["commands"][0].startswith("python3 -m")
    assert by_id["check"]["run_spec"]["commands"][1].startswith("python3 -")
    assert by_id["polish"]["depends_on"] == ["survey"]


def test_baseline_checkpoint_not_duplicated_when_run_precedes_implementation():
    state = _plan_to_todo_state(
        "1. Inspect train files\n"
        "2. Run bash train/train.sh and bash eval.sh\n"
        "3. Implement optimizer\n"
    )
    updated = _ensure_baseline_checkpoint(state)
    assert [task["task_id"] for task in updated["tasks"]].count("baseline") == 0
    assert [task["type"] for task in updated["tasks"]] == ["analysis", "validation", "implementation"]


def _make_loop(tmp_path):
    (tmp_path / "program.md").write_text("Goal: maximize accuracy\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    return AutoResearchLoop(settings)


def test_plan_handler_writes_belief_plan_auto_and_transcript(tmp_path):
    chat, _ = _fake_chat_factory()
    loop = _make_loop(tmp_path)
    program_text = ensure_program_scaffold((tmp_path / "program.md").read_text(encoding="utf-8"))
    project_text = "# Project State\n\n## 当前计划\n(no plan yet)\n"
    ctx = PhaseContext(
        phase="plan",
        root=tmp_path,
        program_text=program_text,
        project_text=project_text,
        signals=PhaseSignals(phase="plan"),
        loop=loop,
    )
    handler = make_plan_handler(chat)
    result = handler(ctx)

    # belief updated in L1
    assert result.program_text is not None
    assert "warmup" in split_program(result.program_text).belief
    # coarse plan in project.md
    assert "add LR warmup" in result.project_text
    # detailed plan in .auto/plan.md
    assert (tmp_path / ".auto" / "plan.md").exists()
    assert "edit train config" in (tmp_path / ".auto" / "plan.md").read_text(encoding="utf-8")
    # structured task state is now the machine-readable handoff
    todo_state = load_todo_state(tmp_path)
    assert [t["task_id"] for t in todo_state["tasks"]] == ["baseline", "t1", "t2", "t3"]
    assert todo_state["tasks"][0]["type"] == "validation"
    assert todo_state["tasks"][1]["goal"] == "edit train config"
    assert todo_state["tasks"][2]["type"] == "validation"
    assert todo_state["tasks"][2]["run_spec"]["commands"] == ["bash train/train.sh", "bash eval.sh"]
    assert todo_state["tasks"][2]["depends_on"] == ["t1"]
    assert todo_state["tasks"][3]["depends_on"] == []
    # transcript archived to L4, not project.md
    artifacts = list((tmp_path / ".autoresearch" / "artifacts").glob("*plan_debate*"))
    assert artifacts
    assert "cosine warmup" not in result.project_text  # persona detail stays out of L2


def test_plan_handler_no_llm_is_deterministic_noop(tmp_path):
    program_text = ensure_program_scaffold("Goal: maximize accuracy\n")
    # loop=None and chat=None => no client can be built => deterministic note
    ctx = PhaseContext(
        phase="plan",
        root=tmp_path,
        program_text=program_text,
        project_text="# Project State\n",
        signals=PhaseSignals(phase="plan"),
        loop=None,
    )
    handler = make_plan_handler(None)
    result = handler(ctx)
    assert "plan" in result.summary
    assert (tmp_path / ".auto" / "plan.md").exists()


def test_plan_handler_framework_deadline_records_fallback_plan(tmp_path):
    loop = _make_loop(tmp_path)
    loop.settings.llm_request_timeout = 0.05
    loop.settings.plan_max_personas = 1
    program_text = ensure_program_scaffold((tmp_path / "program.md").read_text(encoding="utf-8"))

    def slow_chat(system, user):  # noqa: ARG001
        time.sleep(1.0)
        return "{}"

    ctx = PhaseContext(
        phase="plan",
        root=tmp_path,
        program_text=program_text,
        project_text="# Project State\n\n## 当前计划\nold\n",
        signals=PhaseSignals(phase="plan"),
        loop=loop,
    )
    started = time.time()
    result = make_plan_handler(slow_chat)(ctx)
    assert time.time() - started < 0.5
    assert "plan_set" in result.summary
    state = load_todo_state(tmp_path)
    assert state["tasks"]


def test_plan_handler_readonly_program_skips_belief(tmp_path):
    chat, _ = _fake_chat_factory()
    loop = _make_loop(tmp_path)
    # program without markers = read-only constitution
    ctx = PhaseContext(
        phase="plan",
        root=tmp_path,
        program_text="Goal only, no markers",
        project_text="# Project State\n\n## 当前计划\nold\n",
        signals=PhaseSignals(phase="plan"),
        loop=loop,
    )
    handler = make_plan_handler(chat)
    result = handler(ctx)
    # belief update skipped (program_text None) but plan still updated
    assert result.program_text is None
    assert "add LR warmup" in result.project_text


def test_plan_handler_preserves_existing_todo_progress(tmp_path):
    chat, _ = _fake_chat_factory()
    loop = _make_loop(tmp_path)
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "old", "goal": "edit train config", "status": "verified", "last_result": {"ok": True}},
        ]
    })
    program_text = ensure_program_scaffold((tmp_path / "program.md").read_text(encoding="utf-8"))
    ctx = PhaseContext(
        phase="plan",
        root=tmp_path,
        program_text=program_text,
        project_text="# Project State\n\n## 当前计划\nold\n",
        signals=PhaseSignals(phase="plan"),
        loop=loop,
    )
    make_plan_handler(chat)(ctx)
    state = load_todo_state(tmp_path)
    edited = next(task for task in state["tasks"] if task["goal"] == "edit train config")
    assert edited["status"] == "verified"
    assert edited["last_result"] == {"ok": True}
