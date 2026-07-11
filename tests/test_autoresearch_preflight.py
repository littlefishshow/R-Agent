import subprocess

from autoresearch.autoresearch_preflight import git_preflight


def test_git_preflight_non_repo_warns(tmp_path):
    result = git_preflight(tmp_path)
    assert result["git_available"] is False
    assert result["warnings"]


def test_git_preflight_standalone_clean_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    result = git_preflight(tmp_path)
    assert result["git_available"] is True
    assert result["standalone"] is True
    assert result["has_head"] is True
    assert result["dirty"] is False
    assert result["warnings"] == []


def test_git_preflight_nested_repo_warns(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    child = tmp_path / "child"
    child.mkdir()
    result = git_preflight(child)
    assert result["git_available"] is True
    assert result["standalone"] is False
    assert any("inside another git repo" in w for w in result["warnings"])
