import json
import time

from tools import sys_tools
from tools.registry import registry


def _inner(result_json: str) -> dict:
    outer = json.loads(result_json)
    assert outer.get("success") is True
    return json.loads(outer["result"])


def test_run_command_approval_token_survives_isolated_process_and_is_single_use():
    registry.reload_all()
    sys_tools._write_approval_store({})
    cmd = "npm install --help >/dev/null"

    first = _inner(registry.execute_tool_isolated(
        "run_command",
        json.dumps({"command": cmd, "timeout": 10}),
    ))

    assert first["permission_required"] is True
    token = first["approval_token"]
    assert token in sys_tools._read_approval_store()

    second = _inner(registry.execute_tool_isolated(
        "run_command",
        json.dumps({
            "command": cmd,
            "timeout": 10,
            "allow_high_privilege": True,
            "approval_token": token,
        }),
    ))

    assert second["returncode"] == 0
    assert "permission_required" not in second
    assert token not in sys_tools._read_approval_store()

    third = _inner(registry.execute_tool_isolated(
        "run_command",
        json.dumps({
            "command": cmd,
            "timeout": 10,
            "allow_high_privilege": True,
            "approval_token": token,
        }),
    ))

    assert third["permission_required"] is True
    assert third["approval_token"] != token


def test_run_command_approval_token_survives_synchronous_execution_and_is_single_use():
    registry.reload_all()
    sys_tools._write_approval_store({})
    cmd = "npm install --help >/dev/null"

    first = json.loads(sys_tools.run_command_tool(cmd, timeout=10))

    assert first["permission_required"] is True
    token = first["approval_token"]
    assert token in sys_tools._read_approval_store()

    second = json.loads(sys_tools.run_command_tool(
        cmd,
        timeout=10,
        allow_high_privilege=True,
        approval_token=token,
    ))

    assert second["returncode"] == 0
    assert "permission_required" not in second
    assert token not in sys_tools._read_approval_store()

    third = json.loads(sys_tools.run_command_tool(
        cmd,
        timeout=10,
        allow_high_privilege=True,
        approval_token=token,
    ))

    assert third["permission_required"] is True
    assert third["approval_token"] != token


def test_expired_approval_token_is_pruned_from_shared_store():
    sys_tools._write_approval_store({
        "expired-token": {
            "command": "npm install --help >/dev/null",
            "cwd": sys_tools.os.getcwd(),
            "reasons": ["Node 包管理命令会修改依赖或全局环境"],
            "created_at": time.time() - 100,
            "expires_at": time.time() - 1,
        }
    })

    approvals = sys_tools._read_approval_store()

    assert "expired-token" not in approvals


def test_low_risk_command_does_not_create_approval_store_entry():
    sys_tools._write_approval_store({})

    result = json.loads(sys_tools.run_command_tool("printf ok", timeout=10))

    assert result["returncode"] == 0
    assert result["stdout"] == "ok"
    assert sys_tools._read_approval_store() == {}
