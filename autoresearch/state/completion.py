from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


_CRITERIA_HEADING_RE = re.compile(
    r"^\s{0,3}#{1,4}\s+.*(?:success|solved|complete|completion|criteria|"
    r"成功|完成标准|完成条件|解决|验收).*$",
    re.IGNORECASE | re.MULTILINE,
)
_FALLBACK_CRITERIA_HEADING_RE = re.compile(
    r"^\s{0,3}#{1,4}\s+.*(?:stop|stopping|停止|目标).*$",
    re.IGNORECASE | re.MULTILINE,
)
_NEXT_HEADING_RE = re.compile(r"^\s{0,3}#{1,4}\s+", re.MULTILINE)
_NUMBER_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


@dataclass(frozen=True)
class CompletionCriteria:
    metric_name: str
    threshold: float
    operator: str
    higher_is_better: Optional[bool]
    source: str


def parse_completion_criteria(program_text: str) -> Optional[CompletionCriteria]:
    """Parse project-owned completion criteria from program.md.

    The generic framework should not decide what "solved" means. It only reads
    explicit project instructions such as:

    - `z <= 0.001`
    - `primary_metric <= 0.001`
    - `target: 0.001` plus `higher_is_better: false`
    """
    text = str(program_text or "")
    section = _criteria_section(text)
    if not section:
        return None

    metric_name = _parse_metric_name(section) or _parse_metric_name(text) or "primary_metric"
    higher = _parse_higher_is_better(section)
    if higher is None:
        higher = _parse_higher_is_better(text)

    expression = _parse_metric_expression(section)
    if expression is not None:
        expr_metric, operator, threshold = expression
        if expr_metric:
            metric_name = expr_metric
        return CompletionCriteria(
            metric_name=metric_name,
            threshold=threshold,
            operator=operator,
            higher_is_better=higher,
            source=section.strip()[:1200],
        )

    threshold = _parse_threshold(section)
    if threshold is None:
        return None
    operator = ">=" if higher is True else "<="
    return CompletionCriteria(
        metric_name=metric_name,
        threshold=threshold,
        operator=operator,
        higher_is_better=higher,
        source=section.strip()[:1200],
    )


def is_metric_solved(metric: float | None, criteria: CompletionCriteria | None) -> bool:
    if metric is None or criteria is None:
        return False
    value = float(metric)
    threshold = float(criteria.threshold)
    if criteria.operator == "<":
        return value < threshold
    if criteria.operator == "<=":
        return value <= threshold
    if criteria.operator == ">":
        return value > threshold
    if criteria.operator == ">=":
        return value >= threshold
    return False


def _criteria_section(text: str) -> str:
    match = _CRITERIA_HEADING_RE.search(text)
    if not match:
        # A generic "Stop conditions" section is not necessarily a metric
        # completion contract. Only use it if it actually contains a parseable
        # metric expression/threshold.
        fallback = _FALLBACK_CRITERIA_HEADING_RE.search(text)
        if not fallback:
            return ""
        start = fallback.start()
        next_match = _NEXT_HEADING_RE.search(text, fallback.end())
        end = next_match.start() if next_match else len(text)
        section = text[start:end]
        if _parse_metric_expression(section) is None and _parse_threshold(section) is None:
            return ""
        return section
    start = match.start()
    next_match = _NEXT_HEADING_RE.search(text, match.end())
    end = next_match.start() if next_match else len(text)
    return text[start:end]


def _parse_metric_name(text: str) -> str:
    patterns = (
        r"(?:metric_name|metric|primary_metric)\s*[:：]\s*`?([A-Za-z_][\w.-]*)`?",
        r"(?:指标|主指标)\s*[:：]\s*`?([A-Za-z_][\w.-]*)`?",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _parse_higher_is_better(text: str) -> Optional[bool]:
    match = re.search(r"(?:higher_is_better|越大越好|higher is better)\s*[:：=]\s*(true|false|yes|no|1|0)", text, re.IGNORECASE)
    if not match:
        if re.search(r"(?:lower is better|越小越好|最小化|minimi[sz]e)", text, re.IGNORECASE):
            return False
        if re.search(r"(?:higher is better|越大越好|最大化|maximi[sz]e)", text, re.IGNORECASE):
            return True
        return None
    return match.group(1).lower() in {"true", "yes", "1"}


def _parse_metric_expression(text: str) -> Optional[tuple[str, str, float]]:
    pattern = rf"`?([A-Za-z_][\w.-]*)`?\s*(<=|>=|<|>)\s*({_NUMBER_RE})"
    match = re.search(pattern, text)
    if not match:
        pattern = rf"(?:primary_metric|metric|指标|主指标)\s*(<=|>=|<|>)\s*({_NUMBER_RE})"
        generic = re.search(pattern, text, re.IGNORECASE)
        if not generic:
            return None
        return "", generic.group(1), float(generic.group(2))
    return match.group(1), match.group(2), float(match.group(3))


def _parse_threshold(text: str) -> Optional[float]:
    pattern = rf"(?:target|threshold|metric_threshold|solved_metric|目标|阈值|停止值|验收值)\s*[:：=]\s*(?:<=|>=|<|>)?\s*({_NUMBER_RE})"
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1))


__all__ = ["CompletionCriteria", "parse_completion_criteria", "is_metric_solved"]
