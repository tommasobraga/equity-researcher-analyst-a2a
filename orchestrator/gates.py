"""Validation gate nodes for the LangGraph pipeline.

Each gate validates the output of the preceding agent before it enters
the next node. Soft gates (data_collector, news_sentiment) always pass
and only enrich the degraded dict. Hard gates (fundamental_analyst,
risk_assessor, report_writer) can route to END or trigger a reflection
retry with structured feedback injected into the agent prompt.
"""
from __future__ import annotations

from typing import Any

import structlog
from pydantic import ValidationError

from shared.pipeline_models import (
    CandidateItem,
    FundamentalsItem,
    NewsItem,
    RiskAssessmentItem,
    ThemeItem,
)
from shared.report import validate_report

log = structlog.get_logger()

PASS  = "pass"
RETRY = "retry"
FAIL  = "fail"


# ------------------------------------------------------------------ #
# DataCollector gate — SOFT (always pass)                             #
# ------------------------------------------------------------------ #

def node_gate_data_collector(state: dict[str, Any]) -> dict[str, Any]:
    clean: list[dict] = []
    violations: list[str] = []
    for item in state.get("fundamentals", []):
        try:
            clean.append(FundamentalsItem.model_validate(item).model_dump())
        except ValidationError as exc:
            ticker = item.get("ticker", "?")
            violations.append(f"{ticker}: {exc.error_count()} field error(s)")
    degraded = dict(state.get("degraded", {}))
    if violations:
        degraded["gate_data_collector"] = "; ".join(violations)
        log.warning("gate.soft_violations", gate="data_collector", count=len(violations))
    return {"fundamentals": clean, "degraded": degraded}


def route_gate_data_collector(state: dict[str, Any]) -> str:
    return PASS


# ------------------------------------------------------------------ #
# NewsSentiment gate — SOFT (always pass)                             #
# ------------------------------------------------------------------ #

def node_gate_news_sentiment(state: dict[str, Any]) -> dict[str, Any]:
    violations: list[str] = []
    clean_news: list[dict] = []
    clean_themes: list[dict] = []
    for item in state.get("news", []):
        try:
            clean_news.append(NewsItem.model_validate(item).model_dump())
        except ValidationError as exc:
            violations.append(f"news item: {exc.error_count()} field error(s)")
    for item in state.get("themes", []):
        try:
            clean_themes.append(ThemeItem.model_validate(item).model_dump())
        except ValidationError as exc:
            violations.append(f"theme item: {exc.error_count()} field error(s)")
    degraded = dict(state.get("degraded", {}))
    if violations:
        degraded["gate_news_sentiment"] = "; ".join(violations)
        log.warning("gate.soft_violations", gate="news_sentiment", count=len(violations))
    return {"news": clean_news, "themes": clean_themes, "degraded": degraded}


def route_gate_news_sentiment(state: dict[str, Any]) -> str:
    return PASS


# ------------------------------------------------------------------ #
# FundamentalAnalyst gate — HARD, no retry                            #
# ------------------------------------------------------------------ #

def node_gate_fundamental_analyst(state: dict[str, Any]) -> dict[str, Any]:
    clean: list[dict] = []
    violations: list[str] = []
    for item in state.get("candidates", []):
        try:
            clean.append(CandidateItem.model_validate(item).model_dump())
        except ValidationError as exc:
            ticker = item.get("ticker", "?")
            for err in exc.errors():
                loc = " -> ".join(str(x) for x in err["loc"])
                violations.append(f"{ticker}: {loc}: {err['msg']}")
    degraded = dict(state.get("degraded", {}))
    if violations:
        degraded["gate_fundamental_analyst"] = "; ".join(violations)
        log.error("gate.hard_violations", gate="fundamental_analyst", violations=violations)
    if not clean:
        degraded["gate_fundamental_analyst_fatal"] = "No valid candidates after gate validation"
        log.error("gate.fatal", gate="fundamental_analyst")
    return {"candidates": clean, "degraded": degraded}


def route_gate_fundamental_analyst(state: dict[str, Any]) -> str:
    if not state.get("candidates") or "gate_fundamental_analyst_fatal" in state.get("degraded", {}):
        return FAIL
    return PASS


# ------------------------------------------------------------------ #
# RiskAssessor gate — HARD, with reflection retry (max 1)             #
# ------------------------------------------------------------------ #

def node_gate_risk_assessor(state: dict[str, Any]) -> dict[str, Any]:
    candidate_tickers = {c.get("ticker") for c in state.get("candidates", []) if c.get("ticker")}
    clean: list[dict] = []
    violations: list[str] = []
    covered: set[str] = set()

    for item in state.get("risk_assessment", []):
        try:
            validated = RiskAssessmentItem.model_validate(item)
            clean.append(validated.model_dump())
            covered.add(validated.ticker)
        except ValidationError as exc:
            ticker = item.get("ticker", "?")
            for err in exc.errors():
                loc = " -> ".join(str(x) for x in err["loc"])
                violations.append(f"{ticker}: {loc}: {err['msg']}")

    missing = candidate_tickers - covered
    if missing:
        violations.append(f"missing assessment for: {', '.join(sorted(missing))}")

    degraded = dict(state.get("degraded", {}))
    retry_counts = dict(state.get("retry_counts", {}))
    gate_feedback = dict(state.get("gate_feedback", {}))
    has_issues = bool(violations) or not clean

    if has_issues:
        feedback_lines = "\n".join(f"  {i+1}. {v}" for i, v in enumerate(violations)) or "  - no valid risk assessments produced"
        current_retries = retry_counts.get("gate_risk_assessor", 0)

        if current_retries < 1:
            gate_feedback["risk_assessor"] = f"GATE VALIDATION FEEDBACK — fix these issues:\n{feedback_lines}"
            retry_counts["gate_risk_assessor"] = 1
            degraded["gate_risk_assessor"] = f"attempt 1 violations: {'; '.join(violations)}"
            log.warning("gate.retry", gate="risk_assessor", attempt=1, violations=violations)
        elif not clean:
            gate_feedback.pop("risk_assessor", None)
            degraded["gate_risk_assessor_fatal"] = f"No valid assessments after retry: {'; '.join(violations)}"
            log.error("gate.fatal", gate="risk_assessor")
        else:
            gate_feedback.pop("risk_assessor", None)
            degraded["gate_risk_assessor"] = f"partial after retry: {'; '.join(violations)}"
            log.warning("gate.partial_pass", gate="risk_assessor")
    else:
        gate_feedback.pop("risk_assessor", None)
        log.info("gate.pass", gate="risk_assessor", count=len(clean))

    return {
        "risk_assessment": clean,
        "degraded": degraded,
        "retry_counts": retry_counts,
        "gate_feedback": gate_feedback,
    }


def route_gate_risk_assessor(state: dict[str, Any]) -> str:
    if "gate_risk_assessor_fatal" in state.get("degraded", {}):
        return FAIL
    if state.get("gate_feedback", {}).get("risk_assessor"):
        return RETRY
    return PASS


# ------------------------------------------------------------------ #
# ReportWriter gate — HARD, with reflection retry (max 1)             #
# ------------------------------------------------------------------ #

def node_gate_report_writer(state: dict[str, Any]) -> dict[str, Any]:
    all_violations = validate_report(state.get("report", {}))
    errors = [v for v in all_violations if v.severity == "error"]

    degraded = dict(state.get("degraded", {}))
    retry_counts = dict(state.get("retry_counts", {}))
    gate_feedback = dict(state.get("gate_feedback", {}))

    if errors:
        feedback_lines = "\n".join(
            f"  {i+1}. [{v.ticker or 'report'}] {v.message}"
            for i, v in enumerate(errors)
        )
        current_retries = retry_counts.get("gate_report_writer", 0)

        if current_retries < 1:
            gate_feedback["report_writer"] = f"GATE VALIDATION FEEDBACK — fix these errors:\n{feedback_lines}"
            retry_counts["gate_report_writer"] = 1
            degraded["gate_report_writer"] = f"attempt 1 — {len(errors)} error(s)"
            log.warning("gate.retry", gate="report_writer", attempt=1, errors=len(errors))
        else:
            gate_feedback.pop("report_writer", None)
            degraded["gate_report_writer_fatal"] = f"Report has {len(errors)} critical error(s) after retry"
            log.error("gate.fatal", gate="report_writer", errors=len(errors))
    else:
        gate_feedback.pop("report_writer", None)
        warnings = [v for v in all_violations if v.severity == "warning"]
        if warnings:
            degraded["gate_report_writer_warnings"] = f"{len(warnings)} warning(s)"
        log.info("gate.pass", gate="report_writer", warnings=len(warnings) if errors == [] else 0)

    return {
        "degraded": degraded,
        "retry_counts": retry_counts,
        "gate_feedback": gate_feedback,
    }


def route_gate_report_writer(state: dict[str, Any]) -> str:
    if "gate_report_writer_fatal" in state.get("degraded", {}):
        return FAIL
    if state.get("gate_feedback", {}).get("report_writer"):
        return RETRY
    return PASS
