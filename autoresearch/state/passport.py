"""Material passport helpers for AutoResearch artifacts.

The passport is a small, structured provenance header carried by run reports,
experiment records, and verification artifacts.  It follows the same practical
idea as experiment-agent's Material Passport: downstream readers can tell where
an artifact came from, when it was produced, and whether it has been verified
without parsing free-form prose.
"""

from __future__ import annotations

import os
import time
from copy import deepcopy
from typing import Any

PASSPORT_SCHEMA_VERSION = 1
ORIGIN_SKILL = "R-Agent AutoResearch"
VALID_VERIFICATION_STATUSES = {
    "UNVERIFIED",
    "ANALYZED",
    "VERIFIED",
    "CANNOT_VERIFY",
}


def iso_timestamp(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() if ts is None else ts))


def normalize_verification_status(value: str | None) -> str:
    status = str(value or "UNVERIFIED").strip().upper()
    return status if status in VALID_VERIFICATION_STATUSES else "UNVERIFIED"


def build_passport(
    *,
    origin_mode: str,
    project_id: str = "",
    run_id: str = "",
    artifact_type: str = "",
    verification_status: str = "UNVERIFIED",
    version_label: str = "autoresearch_artifact_v1",
    record_id: str = "",
    upstream_dependencies: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict:
    passport = {
        "schema_version": PASSPORT_SCHEMA_VERSION,
        "origin_skill": ORIGIN_SKILL,
        "origin_mode": str(origin_mode or "unknown"),
        "origin_date": iso_timestamp(),
        "verification_status": normalize_verification_status(verification_status),
        "version_label": str(version_label or "autoresearch_artifact_v1"),
        "project_id": str(project_id or ""),
        "run_id": str(run_id or os.environ.get("AUTORESEARCH_RUN_ID", "")),
        "artifact_type": str(artifact_type or ""),
        "record_id": str(record_id or ""),
        "upstream_dependencies": list(upstream_dependencies or []),
    }
    if extra:
        passport["extra"] = deepcopy(extra)
    return passport


def attach_passport(payload: dict, passport: dict) -> dict:
    data = dict(payload or {})
    data["passport"] = dict(passport or {})
    return data


def set_passport_status(payload: dict, status: str) -> dict:
    data = dict(payload or {})
    passport = dict(data.get("passport") or {})
    if not passport:
        passport = build_passport(origin_mode="unknown")
    passport["verification_status"] = normalize_verification_status(status)
    data["passport"] = passport
    return data


def render_passport_markdown(passport: dict) -> str:
    passport = dict(passport or {})
    lines = [
        "## Material Passport",
        "",
        f"- Origin Skill: {passport.get('origin_skill') or ORIGIN_SKILL}",
        f"- Origin Mode: {passport.get('origin_mode') or 'unknown'}",
        f"- Origin Date: {passport.get('origin_date') or iso_timestamp()}",
        f"- Verification Status: {normalize_verification_status(passport.get('verification_status'))}",
        f"- Version Label: {passport.get('version_label') or 'autoresearch_artifact_v1'}",
    ]
    if passport.get("project_id"):
        lines.append(f"- Project ID: {passport.get('project_id')}")
    if passport.get("run_id"):
        lines.append(f"- Run ID: {passport.get('run_id')}")
    if passport.get("record_id"):
        lines.append(f"- Record ID: {passport.get('record_id')}")
    if passport.get("artifact_type"):
        lines.append(f"- Artifact Type: {passport.get('artifact_type')}")
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "ORIGIN_SKILL",
    "PASSPORT_SCHEMA_VERSION",
    "VALID_VERIFICATION_STATUSES",
    "attach_passport",
    "build_passport",
    "iso_timestamp",
    "normalize_verification_status",
    "render_passport_markdown",
    "set_passport_status",
]
