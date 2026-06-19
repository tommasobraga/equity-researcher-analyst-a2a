"""Pydantic models for intermediate pipeline data — used by validation gates."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class FundamentalsItem(BaseModel):
    ticker: str
    price: str = ""
    pe_ttm: str = ""
    eps: str = ""
    analyst_target: str = ""
    error: str = ""


class NewsItem(BaseModel):
    id: str = ""
    title: str = ""
    source: str = ""
    summary: str = ""


class ThemeItem(BaseModel):
    theme_id: str = ""
    title: str = ""
    why_now: str = ""
    evidence: list[str] = Field(default_factory=list)


class CandidateItem(BaseModel):
    ticker: str
    company: str = ""
    market: Literal["US", "EU"] = "US"
    theme_id: str = ""
    thesis: str = ""
    catalyst: str = ""
    news_ids: list[str] = Field(default_factory=list)
    fundamentals: dict = Field(default_factory=dict)
    analyst_consensus: dict = Field(default_factory=dict)

    @field_validator("market", mode="before")
    @classmethod
    def normalize_market(cls, v: object) -> object:
        return v.strip().upper() if isinstance(v, str) else v


class RiskScoringItem(BaseModel):
    forza_catalizzatore: int = Field(default=0, ge=0, le=10)
    fit_orizzonte: int = Field(default=0, ge=0, le=10)
    asimmetria_narrativa: int = Field(default=0, ge=0, le=10)
    qualita_evidenze: int = Field(default=0, ge=0, le=10)
    rischio_crowding: int = Field(default=0, ge=0, le=10)
    totale: int = 0

    @model_validator(mode="after")
    def compute_totale(self) -> "RiskScoringItem":
        self.totale = (
            self.forza_catalizzatore + self.fit_orizzonte
            + self.asimmetria_narrativa + self.qualita_evidenze
            + self.rischio_crowding
        )
        return self


class RiskAssessmentItem(BaseModel):
    ticker: str
    quality: Literal["alta", "media", "bassa", "dati_insufficienti"] = "media"
    scoring: RiskScoringItem = Field(default_factory=RiskScoringItem)
    scenarios: dict = Field(default_factory=dict)
    risks: dict = Field(default_factory=dict)
    falsification: str = ""
    next_checks: list[str] = Field(default_factory=list)

    @field_validator("quality", mode="before")
    @classmethod
    def normalize_quality(cls, v: object) -> object:
        return v.strip().lower() if isinstance(v, str) else v
