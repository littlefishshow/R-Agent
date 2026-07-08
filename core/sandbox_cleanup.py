from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Iterable, Optional


DEFAULT_RETENTION_DAYS = 3.0
DEFAULT_CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60
RETENTION_ENV = "R_AGENT_SANDBOX_RETENTION_DAYS"
INTERVAL_ENV = "R_AGENT_SANDBOX_CLEANUP_INTERVAL_SECONDS"
DISABLE_ENV = "R_AGENT_SANDBOX_CLEANUP_DISABLED"

# Workspace root is the parent of ``core/``.
WORKSPACE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SANDBOX_DIR = WORKSPACE_DIR / "sandbox"

_LAST_CLEANUP_AT: Optional[float] = None


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float_from_env(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value < minimum:
        return minimum
    return value


def get_sandbox_retention_days() -> float:
    """Return sandbox retention days; default is 3 days."""
    return _float_from_env(RETENTION_ENV, DEFAULT_RETENTION_DAYS, minimum=0.0)


def get_cleanup_interval_seconds() -> float:
    """Return opportunistic cleanup interval; default is once per day."""
    return _float_from_env(
        INTERVAL_ENV,
        float(DEFAULT_CLEANUP_INTERVAL_SECONDS),
        minimum=0.0,
    )


def _entry_created_timestamp(path: Path) -> float:
    """Best-effort creation timestamp for a sandbox entry.

    macOS/Windows expose a true birth time as ``st_birthtime``. Many Unix/Linux
    filesystems do not expose birth time through Python's portable ``stat`` API;
    there we intentionally fall back to ``st_ctime`` as the closest available
    metadata timestamp. On Unix this is inode metadata-change time, not creation
    time, so recently touched/renamed entries may be retained longer.
    """
    stat_result = os.stat(path, follow_symlinks=False)
    return float(getattr(stat_result, "st_birthtime", stat_result.st_ctime))


def _iter_sandbox_entries(sandbox_dir: Path) -> Iterable[Path]:
    """Yield all sandbox descendants bottom-up, excluding the sandbox root."""
    try:
        for current_root, dir_names, file_names in os.walk(sandbox_dir, topdown=False, followlinks=False):
            current = Path(current_root)
            for file_name in file_names:
                yield current / file_name
            for dir_name in dir_names:
                yield current / dir_name
    except FileNotFoundError:
        return


def _is_within_sandbox(path: Path, sandbox_root: Path) -> bool:
    try:
        path.resolve().relative_to(sandbox_root)
        return True
    except ValueError:
        return False


def cleanup_sandbox_by_creation_time(
    *,
    sandbox_dir: str | os.PathLike[str] | None = None,
    retention_days: float | None = None,
    now: float | None = None,
) -> dict:
    """Delete sandbox descendants older than ``retention_days``.

    Safety boundaries:
    - The sandbox directory itself is never removed.
    - Every descendant under ``sandbox_dir`` is considered, not only top-level
      entries, so stale nested artifacts are cleaned on startup too.
    - Files and symlinks older than the cutoff are removed directly; symlink
      targets are not followed.
    - Directories are processed bottom-up and removed only when they are both
      older than the cutoff and empty, preventing a stale parent directory from
      deleting fresh children.
    - Non-positive retention values are allowed and mean "older than now".
    """
    root = Path(sandbox_dir) if sandbox_dir is not None else DEFAULT_SANDBOX_DIR
    root = root.resolve()
    current_time = time.time() if now is None else float(now)
    days = get_sandbox_retention_days() if retention_days is None else max(0.0, float(retention_days))
    cutoff = current_time - days * 24 * 60 * 60

    result = {
        "sandbox_dir": str(root),
        "retention_days": days,
        "cutoff": cutoff,
        "deleted": [],
        "kept": [],
        "errors": [],
        "skipped": "",
    }

    if not root.exists():
        result["skipped"] = "sandbox directory does not exist"
        return result
    if not root.is_dir():
        result["skipped"] = "sandbox path is not a directory"
        return result

    for entry in _iter_sandbox_entries(root):
        try:
            # Do not let surprising resolved paths escape the configured sandbox.
            # For symlinks, only the link itself is removed and the target is not
            # followed by stat/unlink/rmtree.
            if not entry.is_symlink() and not _is_within_sandbox(entry, root):
                result["kept"].append(str(entry))
                continue
            if entry.is_symlink() and entry.parent.resolve() != root and not _is_within_sandbox(entry.parent, root):
                result["kept"].append(str(entry))
                continue

            created_at = _entry_created_timestamp(entry)
            if created_at > cutoff:
                result["kept"].append(str(entry))
                continue

            if entry.is_dir() and not entry.is_symlink():
                try:
                    entry.rmdir()
                except OSError:
                    result["kept"].append(str(entry))
                    continue
            else:
                entry.unlink()
            result["deleted"].append(str(entry))
        except Exception as exc:  # best-effort cleanup must not break Agent startup
            result["errors"].append({"path": str(entry), "error": str(exc)})

    return result


def maybe_cleanup_sandbox(*, force: bool = False, now: float | None = None) -> dict:
    """Run sandbox cleanup at most once per configured interval.

    This is intentionally opportunistic: callers can invoke it during startup or
    session creation without spawning a scheduler thread. Set
    ``R_AGENT_SANDBOX_CLEANUP_INTERVAL_SECONDS`` to configure the period, and set
    ``R_AGENT_SANDBOX_CLEANUP_DISABLED=1`` to disable automatic cleanup.
    """
    global _LAST_CLEANUP_AT

    current_time = time.time() if now is None else float(now)
    if _truthy(os.environ.get(DISABLE_ENV, "")):
        return {"skipped": "sandbox cleanup disabled"}

    interval = get_cleanup_interval_seconds()
    if (
        not force
        and _LAST_CLEANUP_AT is not None
        and current_time - _LAST_CLEANUP_AT < interval
    ):
        return {
            "skipped": "cleanup interval has not elapsed",
            "last_cleanup_at": _LAST_CLEANUP_AT,
            "interval_seconds": interval,
        }

    _LAST_CLEANUP_AT = current_time
    return cleanup_sandbox_by_creation_time(now=current_time)
