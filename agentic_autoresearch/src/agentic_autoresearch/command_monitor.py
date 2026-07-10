from __future__ import annotations

import os
import selectors
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from .utils import atomic_write_json


def run_monitored_command(
    root: str | Path,
    command: str,
    *,
    timeout_seconds: int = 300,
    kind: str = "command",
    heartbeat_seconds: float = 1.0,
    tail_chars: int = 4000,
) -> dict[str, Any]:
    """Run a command while writing a heartbeat status file.

    This is for long train/eval commands. The caller still waits synchronously,
    but another process can inspect ``.autoresearch/commands/<id>.json`` to see
    whether the command is running, stale, timed out, and what output has been
    produced so far.
    """
    root = Path(root).expanduser().resolve()
    command_id = f"{kind}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    status_path = root / ".autoresearch" / "commands" / f"{command_id}.json"
    started = time.time()
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    env = _command_env(root)
    proc = subprocess.Popen(
        command,
        cwd=str(root),
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
        start_new_session=True,
        env=env,
    )

    def snapshot(status: str, *, returncode=None, timed_out: bool = False) -> dict[str, Any]:
        now = time.time()
        out = _tail("".join(stdout_chunks), tail_chars)
        err = _tail("".join(stderr_chunks), tail_chars)
        data = {
            "command_id": command_id,
            "kind": kind,
            "command": command,
            "cwd": str(root),
            "pid": proc.pid,
            "status": status,
            "returncode": returncode,
            "timed_out": bool(timed_out),
            "started_at": started,
            "updated_at": now,
            "duration_seconds": round(now - started, 3),
            "heartbeat_age_seconds": 0.0,
            "stdout_tail": out,
            "stderr_tail": err,
            "stdout_chars": sum(len(c) for c in stdout_chunks),
            "stderr_chars": sum(len(c) for c in stderr_chunks),
            "status_path": str(status_path),
        }
        atomic_write_json(status_path, data)
        return data

    snapshot("running")
    selector = selectors.DefaultSelector()
    if proc.stdout is not None:
        selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
    if proc.stderr is not None:
        selector.register(proc.stderr, selectors.EVENT_READ, "stderr")

    timed_out = False
    last_heartbeat = 0.0
    try:
        while True:
            now = time.time()
            if now - started > max(1, int(timeout_seconds or 300)):
                timed_out = True
                _terminate_process_group(proc)
                break

            for key, _mask in selector.select(timeout=0.1):
                stream = key.fileobj
                line = stream.readline()
                if not line:
                    try:
                        selector.unregister(stream)
                    except Exception:
                        pass
                    continue
                if key.data == "stdout":
                    stdout_chunks.append(line)
                else:
                    stderr_chunks.append(line)

            if now - last_heartbeat >= max(0.2, float(heartbeat_seconds or 1.0)):
                snapshot("running")
                last_heartbeat = now

            if proc.poll() is not None:
                break
    finally:
        try:
            selector.close()
        except Exception:
            pass

    # Drain any remaining output.
    try:
        out, err = proc.communicate(timeout=1)
        if out:
            stdout_chunks.append(out)
        if err:
            stderr_chunks.append(err)
    except Exception:
        pass

    returncode = proc.returncode
    status = "timeout" if timed_out else ("ok" if returncode == 0 else "failed")
    return snapshot(status, returncode=returncode, timed_out=timed_out)


def read_command_status(root: str | Path, command_id: str = "", *, latest: bool = False) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    base = root / ".autoresearch" / "commands"
    if latest:
        files = sorted(base.glob("*.json"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        path = files[0] if files else None
    elif command_id:
        path = base / f"{command_id}.json"
    else:
        path = None
    if path is None or not path.exists():
        return {"status": "unknown", "reason": "no command status found"}
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("status") == "running":
        data["heartbeat_age_seconds"] = round(time.time() - float(data.get("updated_at") or 0), 3)
        data["stale"] = data["heartbeat_age_seconds"] > 2 * 60
    return data


def _tail(text: str, limit: int) -> str:
    limit = max(0, int(limit or 0))
    if not limit or len(text) <= limit:
        return text
    return text[-limit:]


def _terminate_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, 15)
        time.sleep(0.5)
        if proc.poll() is None:
            os.killpg(proc.pid, 9)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


def _command_env(root: Path) -> dict[str, str]:
    """Return an env where bare `python` resolves to python3.

    Several benchmark scripts use `python` in shell wrappers. On this host that
    can be Python 2, while the benchmark code uses Python 3 syntax. A tiny shim
    keeps project files unchanged and makes train/eval wrappers run consistently.
    """
    env = dict(os.environ)
    shim = root / ".autoresearch" / "bin"
    shim.mkdir(parents=True, exist_ok=True)
    python = shim / "python"
    if not python.exists():
        python.write_text("#!/usr/bin/env bash\nexec python3 \"$@\"\n", encoding="utf-8")
        python.chmod(0o755)
    env["PATH"] = str(shim) + os.pathsep + env.get("PATH", "")
    return env
