"""Deterministic constraint validators — zero LLM cost."""
from __future__ import annotations

import re
from dataclasses import dataclass

from shared.models import Report


@dataclass
class Violation:
    rule: str
    severity: str   # "error" | "warning"
    ticker: str | None
    message: str

    def as_dict(self) -> dict:
        return {"rule": self.rule, "severity": self.severity, "ticker": self.ticker, "message": self.message}


_CRYPTO_KEYWORDS = {
    "btc", "bitcoin", "eth", "ethereum", "crypto", "cryptocurrency",
    "defi", "nft", "blockchain", "web3", "token", "altcoin",
    "binance", "solana", "cardano", "ripple", "xrp", "dogecoin",
    "stablecoin", "litecoin", "polkadot", "avalanche",
}

_CRYPTO_RE = re.compile(
    r"\b(" + "|".join(re.escape(kw) for kw in _CRYPTO_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

_DIRECTIVE_RE = re.compile(
    r"\b(comprat[eio]|vendete?|acquistat[eio]|shortate?|"
    r"buy\s+now|sell\s+now|acquistare\s+subito|comprare\s+subito|"
    r"investite\s+in|entrate\s+su)"
    r"(?:lo|la|li|le|ne|ci|vi|gli|mi|ti|si)?\b",
    re.IGNORECASE,
)

_NEWS_ID_RE = re.compile(r"^N\d+$")

_SCORING_DIMS = [
    "forza_catalizzatore", "fit_orizzonte", "asimmetria_narrativa",
    "qualita_evidenze", "rischio_crowding",
]

_VALID_GIUDIZI = {"strong buy", "buy", "hold", "sell", "strong sell", "n/a"}


def _full_text(c) -> str:
    parts = [
        c.tesi, c.catalizzatore, c.trigger_falsificazione,
        c.scenari.base, c.scenari.bull, c.scenari.bear,
        c.rischi.macro, c.rischi.settore, c.rischi.azienda,
        c.rischi.regolatorio, c.rischi.valutazione,
    ] + c.prossime_verifiche
    return " ".join(p for p in parts if p)


def _full_text_tema(t) -> str:
    parts = [t.titolo, t.perche_ora] + list(t.indicatori_da_monitorare)
    return " ".join(p for p in parts if p)


_LSE_RE = re.compile(r"\.L$", re.IGNORECASE)
_TICKER_FORMAT_RE = re.compile(r"^[A-Z0-9.\-]+$")


def validate_tickers(tickers: list[str]) -> list[str]:
    """Check tickers against universe constraints before the pipeline starts.

    Returns a list of error strings; empty list = all valid.
    Empty tickers input is allowed (news-driven opportunistic mode).
    """
    errors: list[str] = []
    for ticker in tickers:
        t = ticker.upper().strip()
        if _LSE_RE.search(t):
            errors.append(f"{ticker}: LSE (UK) equity — excluded from universe")
        crypto_hits = {m.group().lower() for m in _CRYPTO_RE.finditer(t)}
        if crypto_hits:
            errors.append(f"{ticker}: crypto keyword detected ({', '.join(sorted(crypto_hits))}) — excluded from universe")
        if not _TICKER_FORMAT_RE.match(t):
            errors.append(f"{ticker}: invalid ticker format (allowed: letters, digits, dot, hyphen)")
    return errors


def validate(report: Report | None) -> list[Violation]:
    violations: list[Violation] = []

    if report is None:
        violations.append(Violation(
            rule="report_parsable", severity="error", ticker=None,
            message="Report is not parseable: JSON missing, truncated or malformed.",
        ))
        return violations

    if len(report.candidati) > 5:
        violations.append(Violation(
            rule="candidate_count", severity="warning", ticker=None,
            message=f"Report contains {len(report.candidati)} candidates (maximum 5).",
        ))

    for c in report.candidati:
        text = _full_text(c)

        if c.ticker.upper().endswith(".L"):
            violations.append(Violation(rule="no_uk_stocks", severity="error", ticker=c.ticker,
                message=f"{c.ticker}: LSE (UK) stock — excluded from universe."))

        crypto_hits = {m.group().lower() for m in _CRYPTO_RE.finditer(f"{c.ticker} {c.azienda}")}
        if crypto_hits:
            violations.append(Violation(rule="no_crypto", severity="error", ticker=c.ticker,
                message=f"{c.ticker}: crypto keyword detected ({', '.join(sorted(crypto_hits))})."))

        match = _DIRECTIVE_RE.search(text)
        if match:
            violations.append(Violation(rule="no_buy_sell_directives", severity="error", ticker=c.ticker,
                message=f"{c.ticker}: explicit buy/sell directive found — '{match.group()}'."))

        if len(c.evidenze_citate) < 2:
            violations.append(Violation(rule="citation_count", severity="warning", ticker=c.ticker,
                message=f"{c.ticker}: {len(c.evidenze_citate)} news cited (minimum 2)."))
        for nid in c.evidenze_citate:
            if not _NEWS_ID_RE.match(nid):
                violations.append(Violation(rule="citation_format", severity="warning", ticker=c.ticker,
                    message=f"{c.ticker}: invalid news ID '{nid}' (expected format: N1, N2, ...)."))

        for dim in _SCORING_DIMS:
            val = getattr(c.scoring, dim)
            if not (1 <= val <= 10):
                violations.append(Violation(rule="score_range", severity="error", ticker=c.ticker,
                    message=f"{c.ticker}: scoring.{dim}={val} outside range 1–10."))

        if c.consenso_analisti.giudizio_sintetico.lower() not in _VALID_GIUDIZI:
            violations.append(Violation(rule="consensus_giudizio", severity="warning", ticker=c.ticker,
                message=f"{c.ticker}: giudizio_sintetico='{c.consenso_analisti.giudizio_sintetico}' is not a standard value."))

    for t in report.temi:
        text = _full_text_tema(t)
        crypto_hits = {m.group().lower() for m in _CRYPTO_RE.finditer(text)}
        if crypto_hits:
            violations.append(Violation(rule="no_crypto", severity="error", ticker=None,
                message=f"Theme '{t.tema_id}': crypto keyword detected ({', '.join(sorted(crypto_hits))})."))
        match = _DIRECTIVE_RE.search(text)
        if match:
            violations.append(Violation(rule="no_buy_sell_directives", severity="error", ticker=None,
                message=f"Theme '{t.tema_id}': explicit buy/sell directive found — '{match.group()}'."))

    return violations
