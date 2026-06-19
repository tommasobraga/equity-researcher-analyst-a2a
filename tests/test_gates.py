"""Unit tests for validation gate nodes and routers — no LLM, no HTTP."""
import pytest

from orchestrator.gates import (
    FAIL,
    PASS,
    RETRY,
    node_gate_data_collector,
    node_gate_fundamental_analyst,
    node_gate_news_sentiment,
    node_gate_report_writer,
    node_gate_risk_assessor,
    route_gate_data_collector,
    route_gate_fundamental_analyst,
    route_gate_news_sentiment,
    route_gate_report_writer,
    route_gate_risk_assessor,
)


def _apply(state: dict, patch: dict) -> dict:
    """Merge gate node output into state copy, mimicking LangGraph."""
    return {**state, **patch}


# ── DataCollector gate (soft) ──────────────────────────────────────────────

class TestGateDataCollector:
    def test_valid_item_passes_clean(self):
        state = {"fundamentals": [{"ticker": "AAPL"}]}
        patch = node_gate_data_collector(state)
        assert len(patch["fundamentals"]) == 1
        assert patch["fundamentals"][0]["ticker"] == "AAPL"
        assert "gate_data_collector" not in patch.get("degraded", {})

    def test_item_missing_ticker_is_soft_filtered(self):
        state = {"fundamentals": [{"price": "$150"}]}
        patch = node_gate_data_collector(state)
        assert patch["fundamentals"] == []
        assert "gate_data_collector" in patch["degraded"]

    def test_empty_fundamentals_passes_without_violations(self):
        state = {"fundamentals": []}
        patch = node_gate_data_collector(state)
        assert patch["fundamentals"] == []
        assert not patch.get("degraded", {})

    def test_mixed_valid_and_invalid_keeps_valid_only(self):
        state = {"fundamentals": [{"ticker": "AAPL"}, {"price": "no-ticker"}]}
        patch = node_gate_data_collector(state)
        assert len(patch["fundamentals"]) == 1
        assert patch["fundamentals"][0]["ticker"] == "AAPL"
        assert "gate_data_collector" in patch["degraded"]

    def test_route_always_pass(self):
        assert route_gate_data_collector({}) == PASS
        assert route_gate_data_collector({"degraded": {"gate_data_collector": "x"}}) == PASS


# ── NewsSentiment gate (soft) ──────────────────────────────────────────────

class TestGateNewsSentiment:
    def test_valid_news_and_themes_pass(self):
        state = {
            "news": [{"id": "N1", "title": "Apple AI", "source": "Reuters", "summary": "Positive"}],
            "themes": [{"theme_id": "AI", "title": "AI wave", "why_now": "acceleration"}],
        }
        patch = node_gate_news_sentiment(state)
        assert len(patch["news"]) == 1
        assert len(patch["themes"]) == 1
        assert not patch.get("degraded", {})

    def test_empty_state_produces_empty_lists(self):
        patch = node_gate_news_sentiment({})
        assert patch["news"] == []
        assert patch["themes"] == []

    def test_route_always_pass(self):
        assert route_gate_news_sentiment({}) == PASS
        assert route_gate_news_sentiment({"degraded": {"gate_news_sentiment": "warn"}}) == PASS


# ── FundamentalAnalyst gate (hard, no retry) ──────────────────────────────

class TestGateFundamentalAnalyst:
    _VALID = {"ticker": "AAPL", "company": "Apple Inc.", "market": "US"}

    def test_valid_candidates_route_pass(self):
        state = {"candidates": [self._VALID]}
        patch = node_gate_fundamental_analyst(state)
        merged = _apply(state, patch)
        assert len(patch["candidates"]) == 1
        assert route_gate_fundamental_analyst(merged) == PASS

    def test_empty_candidates_route_fail(self):
        state = {"candidates": []}
        patch = node_gate_fundamental_analyst(state)
        merged = _apply(state, patch)
        assert route_gate_fundamental_analyst(merged) == FAIL
        assert "gate_fundamental_analyst_fatal" in merged["degraded"]

    def test_invalid_market_drops_item_and_routes_fail(self):
        state = {"candidates": [{"ticker": "BTC", "market": "CRYPTO"}]}
        patch = node_gate_fundamental_analyst(state)
        merged = _apply(state, patch)
        assert patch["candidates"] == []
        assert route_gate_fundamental_analyst(merged) == FAIL

    def test_market_lowercase_normalized_to_uppercase(self):
        state = {"candidates": [{"ticker": "AAPL", "market": "us"}]}
        patch = node_gate_fundamental_analyst(state)
        assert patch["candidates"][0]["market"] == "US"

    def test_market_eu_with_whitespace_normalized(self):
        state = {"candidates": [{"ticker": "UCG.MI", "market": " eu "}]}
        patch = node_gate_fundamental_analyst(state)
        assert patch["candidates"][0]["market"] == "EU"

    def test_fatal_key_forces_fail_even_with_candidates(self):
        state = {
            "candidates": [{"ticker": "AAPL"}],
            "degraded": {"gate_fundamental_analyst_fatal": "..."},
        }
        assert route_gate_fundamental_analyst(state) == FAIL


# ── RiskAssessor gate (hard, retry max 1) ────────────────────────────────

_RA_VALID = {
    "ticker": "AAPL",
    "quality": "alta",
    "scoring": {
        "forza_catalizzatore": 8, "fit_orizzonte": 7, "asimmetria_narrativa": 6,
        "qualita_evidenze": 8, "rischio_crowding": 5,
    },
}

_BASE_RA: dict = {
    "candidates": [{"ticker": "AAPL"}],
    "retry_counts": {},
    "gate_feedback": {},
    "degraded": {},
}


class TestGateRiskAssessor:
    def test_valid_assessment_routes_pass(self):
        state = {**_BASE_RA, "risk_assessment": [_RA_VALID]}
        patch = node_gate_risk_assessor(state)
        merged = _apply(state, patch)
        assert route_gate_risk_assessor(merged) == PASS
        assert "gate_risk_assessor_fatal" not in merged.get("degraded", {})

    def test_missing_coverage_triggers_retry_first_attempt(self):
        state = {
            **_BASE_RA,
            "candidates": [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
            "risk_assessment": [_RA_VALID],  # MSFT not covered
        }
        patch = node_gate_risk_assessor(state)
        merged = _apply(state, patch)
        assert route_gate_risk_assessor(merged) == RETRY
        assert merged["gate_feedback"].get("risk_assessor")
        assert merged["retry_counts"]["gate_risk_assessor"] == 1

    def test_no_assessment_triggers_retry_first_attempt(self):
        state = {**_BASE_RA, "risk_assessment": []}
        patch = node_gate_risk_assessor(state)
        merged = _apply(state, patch)
        assert route_gate_risk_assessor(merged) == RETRY

    def test_second_failure_no_clean_items_is_fatal(self):
        state = {
            **_BASE_RA,
            "risk_assessment": [],
            "retry_counts": {"gate_risk_assessor": 1},
        }
        patch = node_gate_risk_assessor(state)
        merged = _apply(state, patch)
        assert route_gate_risk_assessor(merged) == FAIL
        assert "gate_risk_assessor_fatal" in merged["degraded"]

    def test_second_failure_with_partial_clean_routes_pass(self):
        # MSFT still missing after retry, but AAPL is clean → partial pass
        state = {
            **_BASE_RA,
            "candidates": [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
            "risk_assessment": [_RA_VALID],
            "retry_counts": {"gate_risk_assessor": 1},
        }
        patch = node_gate_risk_assessor(state)
        merged = _apply(state, patch)
        assert route_gate_risk_assessor(merged) == PASS
        assert "gate_risk_assessor_fatal" not in merged.get("degraded", {})

    def test_quality_value_normalized_to_lowercase(self):
        ra_uppercase = {**_RA_VALID, "quality": "ALTA"}
        state = {**_BASE_RA, "risk_assessment": [ra_uppercase]}
        patch = node_gate_risk_assessor(state)
        assert patch["risk_assessment"][0]["quality"] == "alta"

    def test_feedback_text_references_gate_name(self):
        state = {**_BASE_RA, "risk_assessment": []}
        patch = node_gate_risk_assessor(state)
        merged = _apply(state, patch)
        feedback = merged["gate_feedback"]["risk_assessor"]
        assert "GATE VALIDATION FEEDBACK" in feedback


# ── ReportWriter gate (hard, retry max 1) ────────────────────────────────

_VALID_REPORT = {
    "candidati": [{
        "ticker": "AAPL",
        "evidenze_citate": ["N1", "N2"],
        "scoring": {
            "forza_catalizzatore": 7, "fit_orizzonte": 7, "asimmetria_narrativa": 7,
            "qualita_evidenze": 7, "rischio_crowding": 7,
        },
        "consenso_analisti": {"giudizio_sintetico": "buy"},
    }]
}

_UK_REPORT = {
    "candidati": [{
        "ticker": "BATS.L",
        "evidenze_citate": ["N1", "N2"],
        "scoring": {
            "forza_catalizzatore": 7, "fit_orizzonte": 7, "asimmetria_narrativa": 7,
            "qualita_evidenze": 7, "rischio_crowding": 7,
        },
        "consenso_analisti": {"giudizio_sintetico": "buy"},
    }]
}

_BASE_RW: dict = {"retry_counts": {}, "gate_feedback": {}, "degraded": {}}


class TestGateReportWriter:
    def test_valid_report_routes_pass(self):
        state = {**_BASE_RW, "report": _VALID_REPORT}
        patch = node_gate_report_writer(state)
        merged = _apply(state, patch)
        assert route_gate_report_writer(merged) == PASS

    def test_uk_stock_triggers_retry_first_attempt(self):
        state = {**_BASE_RW, "report": _UK_REPORT}
        patch = node_gate_report_writer(state)
        merged = _apply(state, patch)
        assert route_gate_report_writer(merged) == RETRY
        assert merged["gate_feedback"].get("report_writer")
        assert merged["retry_counts"]["gate_report_writer"] == 1

    def test_uk_stock_second_attempt_is_fatal(self):
        state = {**_BASE_RW, "report": _UK_REPORT, "retry_counts": {"gate_report_writer": 1}}
        patch = node_gate_report_writer(state)
        merged = _apply(state, patch)
        assert route_gate_report_writer(merged) == FAIL
        assert "gate_report_writer_fatal" in merged["degraded"]

    def test_empty_report_triggers_retry(self):
        state = {**_BASE_RW, "report": {}}
        patch = node_gate_report_writer(state)
        merged = _apply(state, patch)
        assert route_gate_report_writer(merged) == RETRY

    def test_empty_report_second_attempt_is_fatal(self):
        state = {**_BASE_RW, "report": {}, "retry_counts": {"gate_report_writer": 1}}
        patch = node_gate_report_writer(state)
        merged = _apply(state, patch)
        assert route_gate_report_writer(merged) == FAIL

    def test_feedback_references_ticker_and_rule(self):
        state = {**_BASE_RW, "report": _UK_REPORT}
        patch = node_gate_report_writer(state)
        merged = _apply(state, patch)
        feedback = merged["gate_feedback"]["report_writer"]
        assert "GATE VALIDATION FEEDBACK" in feedback
        assert "BATS.L" in feedback
