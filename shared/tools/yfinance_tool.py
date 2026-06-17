"""Market data stub — yfinance rimosso (policy ❌).

yfinance è scraping non ufficiale di Yahoo Finance: nessun SLA, nessuna
licenza d'uso commerciale, dati non certificati per uso regolamentato.
Incompatibile con financial services enterprise e MiFID II.

Queste funzioni sono placeholder fino all'integrazione di un provider
certificato (Refinitiv LSEG, Bloomberg B-PIPE, Alpha Vantage enterprise)
pianificata in Fase 5 del piano di evoluzione enterprise.

In DEMO_MODE gli agenti restituiscono dati pre-confezionati da
agents/{agent}/demo/response.json — queste funzioni non vengono mai chiamate.
"""


def get_stock_fundamentals(ticker: str) -> dict:
    raise NotImplementedError(
        f"Provider dati certificato non ancora configurato (Fase 5). "
        f"Ticker richiesto: {ticker}. "
        f"Impostare DEMO_MODE=true per sviluppo locale. "
        f"Vedi enterprise-evolution-plan.md Fase 5."
    )


def get_stock_fundamentals_text(ticker: str) -> str:
    raise NotImplementedError(
        f"Provider dati certificato non ancora configurato (Fase 5). "
        f"Ticker richiesto: {ticker}. "
        f"Impostare DEMO_MODE=true per sviluppo locale. "
        f"Vedi enterprise-evolution-plan.md Fase 5."
    )
