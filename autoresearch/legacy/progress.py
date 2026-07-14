"""Progress view for the legacy AutoResearch loop."""

from __future__ import annotations

import json
import time
from pathlib import Path

from autoresearch.legacy.services import extract_progress_percent, normalize_versioning_policy
from autoresearch.legacy.types import AutoResearchObservation, AutoResearchSettings

class AutoResearchProgressView:
    """Text-only visual progress dashboard for autoresearch."""

    def __init__(self, settings: AutoResearchSettings):
        self.settings = settings
        self.path = settings.progress_file()

    @staticmethod
    def _bar(percent: int, width: int = 20) -> str:
        percent = max(0, min(100, int(percent)))
        filled = round(width * percent / 100)
        return "█" * filled + "░" * (width - filled)

    def write(
        self,
        *,
        status: str,
        current_step: str,
        round_index: int,
        total_rounds: int,
        observations: list[AutoResearchObservation],
        state: dict,
        artifact_dir: str,
        step_agent_errors: list[str] | None = None,
    ) -> None:
        total = max(1, int(total_rounds or 1))
        overall = min(100, round(max(0, round_index) * 100 / total))
        recent_text = "\n".join(obs.summary for obs in observations[-3:])
        experiment_percent = extract_progress_percent(recent_text)
        if experiment_percent is None:
            experiment_percent = overall
        buckets = state.get("buckets", {}) if isinstance(state, dict) else {}
        plans = buckets.get("modification_plans") or []
        conclusions = buckets.get("conclusions") or []
        completed = [f"- [{obs.status}] {obs.kind}: {obs.summary[:180]}" for obs in observations[-8:]]
        errors = step_agent_errors or []
        eta = self._eta_text(observations, round_index, total)
        log_tail = self._log_tail(observations)
        recent_experiments = (state.get("experiments") or []) if isinstance(state, dict) else []
        last_version = recent_experiments[-1].get("version_summary", "") if recent_experiments else ""
        lines = [
            f"# auto_research Progress — {self.settings.project_id}",
            "",
            f"Updated: {time.strftime('%F %T')}",
            f"Status: **{status}**",
            f"Current step: `{current_step}`",
            f"Versioning policy: `{normalize_versioning_policy(self.settings.versioning_policy)}`",
            f"Last version action: {last_version or '(none yet)'}",
            "",
            f"Overall: {overall}% `{self._bar(overall)}`",
            f"Experiment/Train progress: {experiment_percent}% `{self._bar(experiment_percent)}`",
            f"ETA: {eta}",
            "",
            "## 当前修改计划",
        ]
        lines.extend([f"- {item}" for item in plans[-3:]] if plans else ["- (no modification plan recorded yet)"])
        lines.extend(["", "## 实验进度 / 结论"])
        lines.extend([f"- {item}" for item in conclusions[-3:]] if conclusions else ["- (no conclusions recorded yet)"])
        lines.extend(["", "## 已完成部分"])
        lines.extend(completed if completed else ["- (no completed step yet)"])
        lines.extend(["", "## 最近日志 Tail"])
        lines.extend([f"```text", log_tail or "(no log tail yet)", "```"])
        best = state.get("best_experiment") if isinstance(state, dict) else None
        pareto = state.get("pareto_front") if isinstance(state, dict) else []
        lines.extend(["", "## Evolution summary"])
        if best:
            lines.append(f"- Best: `{best.get('experiment_id')}` decision={best.get('decision')} metrics={json.dumps(best.get('metrics') or {}, ensure_ascii=False)}")
        else:
            lines.append("- Best: (no metric-bearing experiment yet)")
        lines.append(f"- Pareto candidates: {len(pareto or [])}")
        lines.extend(["", "## Artifacts", f"- `{artifact_dir}`"])
        if errors:
            lines.extend(["", "## Step Agent Fallback / Errors", *(f"- {e}" for e in errors[-5:])])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _eta_text(observations: list[AutoResearchObservation], round_index: int, total_rounds: int) -> str:
        if len(observations) < 2:
            return "estimating"
        elapsed = max(0.0, observations[-1].created_at - observations[0].created_at)
        avg = elapsed / max(1, len(observations) - 1)
        remaining = max(0, total_rounds - round_index)
        seconds = int(avg * remaining)
        return f"~{seconds}s remaining"

    @staticmethod
    def _log_tail(observations: list[AutoResearchObservation], max_lines: int = 20) -> str:
        for obs in reversed(observations):
            if not obs.artifact_path:
                continue
            path = Path(obs.artifact_path)
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    text = "\n".join(str(data.get(k, "")) for k in ("stdout", "stderr") if data.get(k)) or text
            except Exception:
                pass
            tail = "\n".join(text.splitlines()[-max_lines:])
            if tail.strip():
                return tail[-4000:]
        return ""

