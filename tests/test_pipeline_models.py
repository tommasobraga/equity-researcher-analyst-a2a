"""Unit tests for pipeline Pydantic models — no LLM, no HTTP."""
import pytest
from pydantic import ValidationError

from shared.pipeline_models import (
    CandidateItem,
    FundamentalsItem,
    NewsItem,
    RiskAssessmentItem,
    RiskScoringItem,
    ThemeItem,
)


# ── FundamentalsItem ───────────────────────────────────────────────────────

class TestFundamentalsItem:
    def test_ticker_required(self):
        with pytest.raises(ValidationError):
            FundamentalsItem()

    def test_minimal_valid(self):
        item = FundamentalsItem(ticker="AAPL")
        assert item.ticker == "AAPL"
        assert item.price == ""
        assert item.error == ""

    def test_all_fields(self):
        item = FundamentalsItem(ticker="MSFT", price="$415", pe_ttm="35.2", eps="$11.40", analyst_target="$470")
        assert item.pe_ttm == "35.2"
        assert item.analyst_target == "$470"


# ── NewsItem ───────────────────────────────────────────────────────────────

class TestNewsItem:
    def test_all_fields_optional(self):
        item = NewsItem()
        assert item.id == ""
        assert item.title == ""
        assert item.source == ""
        assert item.summary == ""

    def test_with_data(self):
        item = NewsItem(id="N1", title="Apple AI", source="Reuters", summary="Positive outlook")
        assert item.id == "N1"
        assert item.source == "Reuters"


# ── ThemeItem ──────────────────────────────────────────────────────────────

class TestThemeItem:
    def test_all_fields_optional(self):
        item = ThemeItem()
        assert item.theme_id == ""
        assert item.evidence == []

    def test_with_evidence(self):
        item = ThemeItem(theme_id="AI", title="AI wave", why_now="compute cost drop", evidence=["N1", "N2"])
        assert len(item.evidence) == 2


# ── CandidateItem ──────────────────────────────────────────────────────────

class TestCandidateItem:
    def test_ticker_required(self):
        with pytest.raises(ValidationError):
            CandidateItem()

    def test_default_market_is_us(self):
        item = CandidateItem(ticker="AAPL")
        assert item.market == "US"

    def test_market_lowercase_normalized(self):
        item = CandidateItem(ticker="AAPL", market="us")
        assert item.market == "US"

    def test_market_eu_with_whitespace_normalized(self):
        item = CandidateItem(ticker="UCG.MI", market=" eu ")
        assert item.market == "EU"

    def test_invalid_market_raises(self):
        with pytest.raises(ValidationError):
            CandidateItem(ticker="BTC", market="CRYPTO")

    def test_optional_fields_default_empty(self):
        item = CandidateItem(ticker="NVDA")
        assert item.thesis == ""
        assert item.news_ids == []
        assert item.fundamentals == {}


# ── RiskScoringItem ────────────────────────────────────────────────────────

class TestRiskScoringItem:
    def test_totale_auto_computed(self):
        item = RiskScoringItem(
            forza_catalizzatore=8, fit_orizzonte=7, asimmetria_narrativa=6,
            qualita_evidenze=8, rischio_crowding=5,
        )
        assert item.totale == 34

    def test_all_zeros_totale_is_zero(self):
        item = RiskScoringItem()
        assert item.totale == 0

    def test_max_scores_totale_is_50(self):
        item = RiskScoringItem(
            forza_catalizzatore=10, fit_orizzonte=10, asimmetria_narrativa=10,
            qualita_evidenze=10, rischio_crowding=10,
        )
        assert item.totale == 50

    def test_score_above_10_raises(self):
        with pytest.raises(ValidationError):
            RiskScoringItem(forza_catalizzatore=11)

    def test_negative_score_raises(self):
        with pytest.raises(ValidationError):
            RiskScoringItem(fit_orizzonte=-1)


# ── RiskAssessmentItem ─────────────────────────────────────────────────────

class TestRiskAssessmentItem:
    def test_ticker_required(self):
        with pytest.raises(ValidationError):
            RiskAssessmentItem()

    def test_default_quality_is_media(self):
        item = RiskAssessmentItem(ticker="AAPL")
        assert item.quality == "media"

    def test_quality_uppercase_normalized(self):
        item = RiskAssessmentItem(ticker="AAPL", quality="ALTA")
        assert item.quality == "alta"

    def test_quality_with_whitespace_normalized(self):
        item = RiskAssessmentItem(ticker="AAPL", quality="  bassa  ")
        assert item.quality == "bassa"

    def test_quality_dati_insufficienti_valid(self):
        item = RiskAssessmentItem(ticker="AAPL", quality="dati_insufficienti")
        assert item.quality == "dati_insufficienti"

    def test_invalid_quality_raises(self):
        with pytest.raises(ValidationError):
            RiskAssessmentItem(ticker="AAPL", quality="excellent")

    def test_scoring_defaults_to_zero_totale(self):
        item = RiskAssessmentItem(ticker="AAPL")
        assert item.scoring.totale == 0

    def test_scoring_totale_computed_from_nested_fields(self):
        item = RiskAssessmentItem(
            ticker="AAPL",
            scoring={"forza_catalizzatore": 9, "fit_orizzonte": 8, "asimmetria_narrativa": 7,
                     "qualita_evidenze": 9, "rischio_crowding": 4},
        )
        assert item.scoring.totale == 37
