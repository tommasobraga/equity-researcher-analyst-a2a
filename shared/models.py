"""Pydantic models for the report JSON schema produced by ReportWriter."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Scoring(BaseModel):
    forza_catalizzatore: int = Field(default=0, ge=0, le=10)
    fit_orizzonte: int = Field(default=0, ge=0, le=10)
    asimmetria_narrativa: int = Field(default=0, ge=0, le=10)
    qualita_evidenze: int = Field(default=0, ge=0, le=10)
    rischio_crowding: int = Field(default=0, ge=0, le=10)
    totale: int = 0

    model_config = {"frozen": False}

    @model_validator(mode="after")
    def compute_totale(self) -> "Scoring":
        self.totale = (
            self.forza_catalizzatore
            + self.fit_orizzonte
            + self.asimmetria_narrativa
            + self.qualita_evidenze
            + self.rischio_crowding
        )
        return self


class Scenari(BaseModel):
    model_config = {"frozen": False}
    base: str = ""
    bull: str = ""
    bear: str = ""


class Rischi(BaseModel):
    model_config = {"frozen": False}
    macro: str = ""
    settore: str = ""
    azienda: str = ""
    regolatorio: str = ""
    valutazione: str = ""


class ConsensoCandidato(BaseModel):
    model_config = {"frozen": False}
    totale_analisti: int = 0
    strong_buy: int = 0
    buy: int = 0
    hold: int = 0
    sell: int = 0
    strong_sell: int = 0
    giudizio_sintetico: str = ""
    target_medio: str = ""


class Candidato(BaseModel):
    model_config = {"frozen": False}

    rank: int = 0
    ticker: str = ""
    azienda: str = ""
    mercato: Literal["US", "EU"] = "US"
    tema: str = ""
    tesi: str = ""
    catalizzatore: str = ""
    orizzonte_settimane: str = ""
    scenari: Scenari = Field(default_factory=Scenari)
    rischi: Rischi = Field(default_factory=Rischi)
    trigger_falsificazione: str = ""
    prossime_verifiche: list[str] = Field(default_factory=list)
    evidenze_citate: list[str] = Field(default_factory=list)
    rating_qualita: Literal["alta", "media", "bassa"] = "media"
    scoring: Scoring = Field(default_factory=Scoring)
    consenso_analisti: ConsensoCandidato = Field(default_factory=ConsensoCandidato)

    @field_validator("mercato", mode="before")
    @classmethod
    def normalize_mercato(cls, v: object) -> object:
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("rating_qualita", mode="before")
    @classmethod
    def normalize_rating(cls, v: object) -> object:
        return v.strip().lower() if isinstance(v, str) else v


class CandidatoEscluso(BaseModel):
    model_config = {"frozen": False}
    ticker: str = ""
    motivo_esclusione: str = ""


class Tema(BaseModel):
    model_config = {"frozen": False}
    tema_id: str = ""
    titolo: str = ""
    perche_ora: str = ""
    evidenze: list[str] = Field(default_factory=list)
    indicatori_da_monitorare: list[str] = Field(default_factory=list)


class Report(BaseModel):
    model_config = {"frozen": False}
    data_analisi: str = ""
    universo: str = ""
    temi: list[Tema] = Field(default_factory=list)
    candidati: list[Candidato] = Field(default_factory=list)
    candidati_esclusi: list[CandidatoEscluso] = Field(default_factory=list)
    nota_metodologica: str = ""


class Correction(BaseModel):
    model_config = {"frozen": False}
    ticker: str
    field: str
    value: int | float | str
    motivo: str = ""


class TaskDecomposition(BaseModel):
    """Structured output of the task decomposer node.

    Extracted from a natural language prompt; used to parameterise the pipeline.
    All fields have safe defaults so the model is always constructable even when
    the LLM returns a partial JSON.
    """
    model_config = {"frozen": False}

    intent: Literal[
        "ticker_analysis",      # "analizza AAPL e MSFT"
        "sector_screen",        # "trova opportunità AI in EU"
        "comparative_analysis", # "confronta momentum di X vs Y"
        "theme_exploration",    # "impatto dei dazi sul tech"
        "portfolio_review",     # "rivedi il portfolio"
    ] = "ticker_analysis"
    tickers: list[str] = Field(default_factory=list)
    mode: Literal["analyze", "portfolio", "full"] = "analyze"
    research_focus: str = ""           # injected into FA and ReportWriter prompts
    sectors: list[str] = Field(default_factory=list)
    horizon_weeks: int | None = None
    constraints: list[str] = Field(default_factory=list)
