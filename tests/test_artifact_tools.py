import json
from pathlib import Path

from tools.artifact_tools import artifact_inspect_tool, artifact_search_tool, artifact_slice_tool


def test_artifact_inspect_search_and_slice(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    artifact = Path("sandbox/tool_outputs/demo.log")
    artifact.parent.mkdir(parents=True)
    artifact.write_text("alpha\nERROR boom\nbeta\nwarning low\n", encoding="utf-8")

    inspected = json.loads(artifact_inspect_tool(str(artifact), sample_lines=2))
    assert inspected["lines"] == 4
    assert "ERROR boom" in inspected["head_sample"]

    searched = json.loads(artifact_search_tool(str(artifact), "error", context_lines=1, limit=5))
    assert searched["total_matches"] == 1
    assert searched["matches"][0]["line"] == 2
    assert "1|alpha" in searched["matches"][0]["context"]

    sliced = json.loads(artifact_slice_tool(str(artifact), offset=2, limit=999))
    assert sliced["limit"] == 500
    assert sliced["limit_capped"] is True
    assert sliced["content"].startswith("2|ERROR boom")


def test_artifact_tools_reject_outside_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    outside = tmp_path.parent / "outside-artifact.txt"
    outside.write_text("secret", encoding="utf-8")

    result = json.loads(artifact_inspect_tool(str(outside)))

    assert "error" in result
    assert "inside workspace" in result["error"]
