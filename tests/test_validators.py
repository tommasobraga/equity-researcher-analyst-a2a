"""Unit tests for deterministic semantic validators — no LLM, no HTTP."""
import pytest

from shared.models import (
    Candidato,
    ConsensoCandidato,
    Report,
    Rischi,
    Scenari,
    Scoring,
    Tema,
)
from shared.validators import validate


# ── Helpers ────────────────────────────────────────────────────────────────

def _scoring(**overrides) -> Scoring:
    defaults = dict(
        forza_catalizzatore=7, fit_orizzonte=7, asimmetria_narrativa=7,
        qualita_evidenze=7, rischio_crowding=7,
    )
    defaults.update(overrides)
    return Scoring(**defaults)


def _candidato(**overrides) -> Candidato:
    defaults = dict(
        ticker="AAPL",
        azienda="Apple Inc.",
        evidenze_citate=["N1", "N2"],
        scoring=_scoring(),
        consenso_analisti=ConsensoCandidato(giudizio_sintetico="buy"),
    )
    defaults.update(overrides)
    return Candidato(**defaults)


def _report(*candidati: Candidato, temi: list[Tema] | None = None) -> Report:
    return Report(candidati=list(candidati), temi=temi or [])


def _violations(report: Report, rule: str) -> list:
    return [v for v in validate(report) if v.rule == rule]


# ── Baseline ───────────────────────────────────────────────────────────────

class TestBaseline:
    def test_clean_report_has_no_violations(self):
        report = _report(_candidato())
        assert validate(report) == []

    def test_validate_none_returns_error(self):
        violations = validate(None)
        assert len(violations) == 1
        assert violations[0].rule == "report_parsable"
        assert violations[0].severity == "error"


# ── no_uk_stocks ───────────────────────────────────────────────────────────

class TestNoUkStocks:
    def test_lse_ticker_is_error(self):
        v = _violations(_report(_candidato(ticker="BATS.L")), "no_uk_stocks")
        assert len(v) == 1
        assert v[0].severity == "error"
        assert v[0].ticker == "BATS.L"

    def test_lowercase_l_suffix_is_error(self):
        v = _violations(_report(_candidato(ticker="VOD.l")), "no_uk_stocks")
        assert len(v) == 1

    def test_us_ticker_with_l_middle_is_clean(self):
        v = _violations(_report(_candidato(ticker="GOOGL")), "no_uk_stocks")
        assert v == []

    def test_eu_ticker_is_clean(self):
        v = _violations(_report(_candidato(ticker="UCG.MI")), "no_uk_stocks")
        assert v == []


# ── no_crypto ──────────────────────────────────────────────────────────────

class TestNoCrypto:
    def test_bitcoin_ticker_is_error(self):
        v = _violations(_report(_candidato(ticker="BTC")), "no_crypto")
        assert len(v) == 1
        assert v[0].severity == "error"

    def test_crypto_in_company_name_is_error(self):
        v = _violations(_report(_candidato(azienda="Ethereum Foundation")), "no_crypto")
        assert len(v) == 1

    def test_defi_keyword_is_error(self):
        v = _violations(_report(_candidato(azienda="DeFi Protocol AG")), "no_crypto")
        assert len(v) == 1

    def test_clean_tech_company_is_clean(self):
        v = _violations(_report(_candidato(ticker="NVDA", azienda="NVIDIA Corporation")), "no_crypto")
        assert v == []

    def test_crypto_in_theme_is_error(self):
        tema = Tema(tema_id="T1", titolo="Bitcoin adoption")
        v = _violations(_report(temi=[tema]), "no_crypto")
        assert len(v) == 1


# ── no_buy_sell_directives ─────────────────────────────────────────────────

class TestNoBuySellDirectives:
    def test_comprate_in_tesi_is_error(self):
        c = _candidato(tesi="Comprate questo titolo subito")
        v = _violations(_report(c), "no_buy_sell_directives")
        assert len(v) == 1
        assert v[0].severity == "error"

    def test_buy_now_in_rischi_is_error(self):
        c = _candidato(rischi=Rischi(macro="buy now before earnings"))
        v = _violations(_report(c), "no_buy_sell_directives")
        assert len(v) == 1

    def test_vendete_in_scenari_is_error(self):
        c = _candidato(scenari=Scenari(bear="vendete immediatamente"))
        v = _violations(_report(c), "no_buy_sell_directives")
        assert len(v) == 1

    def test_analytical_language_is_clean(self):
        c = _candidato(tesi="Strong growth outlook driven by AI infrastructure demand")
        v = _violations(_report(c), "no_buy_sell_directives")
        assert v == []

    def test_directive_in_theme_is_error(self):
        tema = Tema(tema_id="T1", titolo="AI", perche_ora="comprate adesso")
        v = _violations(_report(temi=[tema]), "no_buy_sell_directives")
        assert len(v) == 1

    # Pronominal clitics — Italian report generates forms like "acquistatelo"
    def test_acquistatelo_clitic_is_error(self):
        c = _candidato(tesi="Acquistatelo prima della trimestrale")
        v = _violations(_report(c), "no_buy_sell_directives")
        assert len(v) == 1

    def test_comprateli_clitic_is_error(self):
        c = _candidato(catalizzatore="Comprateli in accumulo graduale")
        v = _violations(_report(c), "no_buy_sell_directives")
        assert len(v) == 1

    def test_vendetela_clitic_is_error(self):
        c = _candidato(scenari=Scenari(bear="Vendetela se scende sotto 150"))
        v = _violations(_report(c), "no_buy_sell_directives")
        assert len(v) == 1

    def test_acquistatene_clitic_is_error(self):
        c = _candidato(tesi="Acquistatene una quota in questo contesto")
        v = _violations(_report(c), "no_buy_sell_directives")
        assert len(v) == 1


# ── citation_count ─────────────────────────────────────────────────────────

class TestCitationCount:
    def test_zero_citations_is_warning(self):
        v = _violations(_report(_candidato(evidenze_citate=[])), "citation_count")
        assert len(v) == 1
        assert v[0].severity == "warning"

    def test_one_citation_is_warning(self):
        v = _violations(_report(_candidato(evidenze_citate=["N1"])), "citation_count")
        assert len(v) == 1

    def test_two_citations_is_clean(self):
        v = _violations(_report(_candidato(evidenze_citate=["N1", "N2"])), "citation_count")
        assert v == []

    def test_three_citations_is_clean(self):
        v = _violations(_report(_candidato(evidenze_citate=["N1", "N2", "N3"])), "citation_count")
        assert v == []


# ── citation_format ────────────────────────────────────────────────────────

class TestCitationFormat:
    def test_invalid_id_format_is_warning(self):
        c = _candidato(evidenze_citate=["N1", "bad-id"])
        v = _violations(_report(c), "citation_format")
        assert len(v) == 1
        assert v[0].severity == "warning"
        assert "bad-id" in v[0].message

    def test_valid_n_format_is_clean(self):
        v = _violations(_report(_candidato(evidenze_citate=["N1", "N42"])), "citation_format")
        assert v == []

    def test_n_without_number_is_invalid(self):
        c = _candidato(evidenze_citate=["N1", "N"])
        v = _violations(_report(c), "citation_format")
        assert len(v) == 1


# ── score_range ────────────────────────────────────────────────────────────

class TestScoreRange:
    def test_score_zero_is_error(self):
        # Scoring allows ge=0 in Pydantic but validator requires 1-10
        c = _candidato(scoring=_scoring(forza_catalizzatore=0))
        v = _violations(_report(c), "score_range")
        assert len(v) == 1
        assert v[0].severity == "error"
        assert "forza_catalizzatore" in v[0].message

    def test_all_scores_in_range_is_clean(self):
        v = _violations(_report(_candidato(scoring=_scoring())), "score_range")
        assert v == []

    def test_multiple_zero_scores_produce_multiple_violations(self):
        c = _candidato(scoring=_scoring(forza_catalizzatore=0, fit_orizzonte=0))
        v = _violations(_report(c), "score_range")
        assert len(v) == 2

    def test_boundary_score_1_is_clean(self):
        c = _candidato(scoring=_scoring(rischio_crowding=1))
        v = _violations(_report(c), "score_range")
        assert v == []

    def test_boundary_score_10_is_clean(self):
        c = _candidato(scoring=_scoring(qualita_evidenze=10))
        v = _violations(_report(c), "score_range")
        assert v == []


# ── consensus_giudizio ─────────────────────────────────────────────────────

class TestConsensusGiudizio:
    @pytest.mark.parametrize("giudizio", ["buy", "sell", "hold", "strong buy", "strong sell", "n/a"])
    def test_valid_giudizi_are_clean(self, giudizio: str):
        c = _candidato(consenso_analisti=ConsensoCandidato(giudizio_sintetico=giudizio))
        v = _violations(_report(c), "consensus_giudizio")
        assert v == []

    def test_unknown_giudizio_is_warning(self):
        c = _candidato(consenso_analisti=ConsensoCandidato(giudizio_sintetico="outperform"))
        v = _violations(_report(c), "consensus_giudizio")
        assert len(v) == 1
        assert v[0].severity == "warning"

    def test_empty_giudizio_is_warning(self):
        c = _candidato(consenso_analisti=ConsensoCandidato(giudizio_sintetico=""))
        v = _violations(_report(c), "consensus_giudizio")
        assert len(v) == 1


# ── candidate_count ────────────────────────────────────────────────────────

class TestCandidateCount:
    def test_five_candidates_is_clean(self):
        tickers = ["AAPL", "MSFT", "NVDA", "UCG.MI", "ASML.AS"]
        report = _report(*[_candidato(ticker=t) for t in tickers])
        v = _violations(report, "candidate_count")
        assert v == []

    def test_six_candidates_is_warning(self):
        tickers = ["AAPL", "MSFT", "NVDA", "UCG.MI", "ASML.AS", "SAP.DE"]
        report = _report(*[_candidato(ticker=t) for t in tickers])
        v = _violations(report, "candidate_count")
        assert len(v) == 1
        assert v[0].severity == "warning"
