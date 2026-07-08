import json
import os
import threading
import time
from typing import Any, Callable, Optional

from rich.console import Console

console = Console()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, "sandbox", "terminal_progress_state.json")
STATE_LOCK = threading.RLock()
STALE_AFTER_SECONDS = 30


def _current_cli_status():
    """Return the active CLI Rich status exposed by main.py/__main__."""
    try:
        import sys

        for module_name in ("__main__", "main"):
            module = sys.modules.get(module_name)
            status = getattr(module, "ACTIVE_STATUS", None) if module is not None else None
            if status is not None:
                return status
    except Exception:
        return None
    return None


def _state_lock_file():
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    lock_path = f"{STATE_FILE}.lock"
    return open(lock_path, "a", encoding="utf-8")


def _load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp_file = f"{STATE_FILE}.tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, STATE_FILE)


def _with_state_lock(fn):
    with STATE_LOCK:
        with _state_lock_file() as lock_file:
            try:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    return fn()
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except ImportError:
                return fn()


def _renderable_line_count(renderable: Any) -> int:
    try:
        return len(console.render_lines(renderable, console.options))
    except Exception:
        return 0


def _terminal_supports_overwrite() -> bool:
    if os.environ.get("RAGENT_TODO_OVERWRITE", "1").lower() in {"0", "false", "no", "off"}:
        return False
    return bool(getattr(console, "is_terminal", False))


def _state_is_fresh(state: dict) -> bool:
    try:
        updated_at = float(state.get("updated_at") or 0)
    except Exception:
        return False
    try:
        stale_after = float(os.environ.get("RAGENT_TODO_OVERWRITE_STALE_SECONDS", STALE_AFTER_SECONDS))
    except Exception:
        stale_after = STALE_AFTER_SECONDS
    return stale_after <= 0 or (time.time() - updated_at) <= stale_after


def _erase_previous_board_if_needed() -> None:
    if not _terminal_supports_overwrite():
        return

    def op():
        state = _load_state()
        line_count = int(state.get("line_count") or 0) if state.get("kind") == "todo_board" and _state_is_fresh(state) else 0
        if line_count > 0:
            # Move up one physical line and clear it, repeated for the old panel.
            # This is intentionally scoped to consecutive Todo boards: ordinary
            # log output marks the state as non-board, so we do not erase logs.
            console.file.write(("\x1b[1A\x1b[2K") * line_count)
            try:
                console.file.flush()
            except Exception:
                pass

    _with_state_lock(op)


def _mark_output(kind: str, line_count: int = 0) -> None:
    def op():
        _save_state({"kind": kind, "line_count": max(0, int(line_count or 0)), "updated_at": time.time()})

    _with_state_lock(op)


def print_after_status(renderable=None, *args, status_getter: Optional[Callable[[], Any]] = None, output_kind: str = "other", **kwargs):
    """Print while pausing the active Rich status; optionally overwrite Todo board.

    Consecutive calls with ``output_kind='todo_board'`` replace the previous Todo
    Progress panel in-place on real terminals. Any non-board print marks the
    terminal stream as ordinary output, preventing later boards from erasing log
    lines that appeared after the previous board.
    """
    status = (status_getter or _current_cli_status)()
    stopped = False
    if status is not None:
        try:
            status.stop()
            stopped = True
        except Exception:
            stopped = False

    try:
        line_count = _renderable_line_count(renderable) if renderable is not None else 1
        if output_kind == "todo_board":
            _erase_previous_board_if_needed()

        if renderable is None:
            console.print(*args, **kwargs)
        else:
            console.print(renderable, *args, **kwargs)

        _mark_output("todo_board" if output_kind == "todo_board" else "other", line_count if output_kind == "todo_board" else 0)
    finally:
        if stopped:
            try:
                status.start()
            except Exception:
                pass
