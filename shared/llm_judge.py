"""LLM Judge — grounding e coerenza semantica del report.

Layer di validazione indipendente dal ReportWriter: verifica che le affermazioni
del report siano tracciabili al materiale sorgente (news, fondamentali, RAG context).

Differenza rispetto al QA pass interno di ReportWriter:
  - QA pass: auto-revisione (stessa classe di agente che ha generato il report)
  - LLM Judge: prospettiva indipendente, accesso ai sorgenti originali

Fase attuale: Anthropic SDK diretto (stesso pattern di ReportWriter).
In DEMO_MODE restituisce PASS simulato senza chiamata LLM.

Upgrade path: nessuna modifica all'interfaccia pubblica (run_judge) per
switchare modello o aggiungere tool-use di grounding.
"""
import json
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from shared.demo import is_demo_mode

log = structlog.get_logger()

_MODEL_ID = "claude-sonnet-4-6"

_JUDGE_SYSTEM = """You are an independent LLM Judge evaluating an equity research report.
You did NOT generate this report — your sole role is evaluation.

You receive:
1. The final report (executive summary + JSON with candidates, scoring, scenarios)
2. The original source material: news items, market fundamentals, internal knowledge base context

GROUNDING CHECKS to perform:
1. CITATION ACCURACY — Do cited news IDs (N1, N2...) exist in the news list and actually support the claims made for that candidate?
2. FUNDAMENTAL ACCURACY — Do numbers cited in the report (P/E, price, EPS, 52w range) match the provided fundamentals data?
3. POLICY COMPLIANCE — Are all candidates in the approved investment universe (US/EU equity, no crypto, no energy/utilities/REIT/consumer staples/airlines)? Use the knowledge base context if available.
4. SCORING CONSISTENCY — Are bull/base/bear scenario narratives consistent with the numerical scores? A score >= 40 cannot have a predominantly negative narrative.
5. UNSUPPORTED CLAIMS — Are material statements made without any cited source?

VERDICT:
- PASS (grounding_score 80-100): All checks pass or only trivial issues found.
- WARN (grounding_score 50-79): Minor issues (1-2 unsupported claims, small number discrepancy, narrative slightly inconsistent with score).
- FAIL (grounding_score 0-49): Serious failures (fabricated news IDs, wrong fundamentals, policy violations, major scoring inconsistency).

Respond ONLY with valid JSON (no markdown fences):
{
  "verdict": "PASS|WARN|FAIL",
  "grounding_score": 0-100,
  "issues": [
    {
      "type": "citation_missing|fundamental_mismatch|policy_violation|unsupported_claim|scoring_inconsistency",
      "ticker": "TICKER or null",
      "detail": "one sentence"
    }
  ],
  "summary": "2-3 sentences overall assessment"
}"""


@dataclass
class JudgmentResult:
    verdict: str = "PASS"
    grounding_score: int = 100
    issues: list[dict] = field(default_factory=list)
    summary: str = ""
    demo: bool = False

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "grounding_score": self.grounding_score,
            "issues": self.issues,
            "summary": self.summary,
            "demo": self.demo,
        }


def _build_user_prompt(
    executive_summary: str,
    report_dict: dict,
    news: list,
    fundamentals: list,
    rag_context: str,
) -> str:
    parts = []
    if rag_context:
        parts.append(f"INTERNAL KNOWLEDGE BASE (investment policy, sector notes):\n{rag_context}")
    parts.append(f"NEWS ITEMS (source material, max 15):\n{json.dumps(news[:15], ensure_ascii=False)}")
    parts.append(f"FUNDAMENTALS (source material):\n{json.dumps(fundamentals, ensure_ascii=False)}")
    parts.append(f"EXECUTIVE SUMMARY:\n{executive_summary}")
    parts.append(f"REPORT JSON:\n{json.dumps(report_dict, ensure_ascii=False, indent=2)}")
    parts.append("Evaluate grounding and return your JSON verdict.")
    return "\n\n---\n\n".join(parts)


async def run_judge(
    client: Any,
    executive_summary: str,
    report_dict: dict,
    news: list,
    fundamentals: list,
    rag_context: str = "",
    model: str = _MODEL_ID,
    correlation_id: str | None = None,
) -> JudgmentResult:
    """Esegue il grounding check del report contro i sorgenti originali.

    In DEMO_MODE restituisce PASS simulato senza chiamata LLM.
    In caso di errore restituisce WARN con degraded flag — non blocca la pipeline.
    """
    if is_demo_mode():
        result = JudgmentResult(
            verdict="PASS",
            grounding_score=95,
            issues=[],
            summary="Demo mode: grounding check simulato. Report conforme alle linee guida di investimento.",
            demo=True,
        )
        log.info("judge.demo", correlation_id=correlation_id)
        return result

    if not report_dict:
        return JudgmentResult(
            verdict="WARN",
            grounding_score=0,
            issues=[{"type": "unsupported_claim", "ticker": None,
                     "detail": "Report assente o non parsabile — grounding non eseguibile"}],
            summary="Report assente. Impossibile valutare il grounding.",
        )

    t0 = time.monotonic()
    user_prompt = _build_user_prompt(executive_summary, report_dict, news, fundamentals, rag_context)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=_JUDGE_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = response.content[0].text.strip()

        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end <= start:
            raise ValueError("Nessun oggetto JSON nel verdict del judge")
        data = json.loads(raw[start:end])

        result = JudgmentResult(
            verdict=data.get("verdict", "WARN"),
            grounding_score=int(data.get("grounding_score", 50)),
            issues=data.get("issues", []),
            summary=data.get("summary", ""),
        )
        log.info(
            "judge.completed",
            verdict=result.verdict,
            grounding_score=result.grounding_score,
            n_issues=len(result.issues),
            duration_ms=int((time.monotonic() - t0) * 1000),
            correlation_id=correlation_id,
        )
        return result

    except Exception as e:
        log.warning("judge.failed", error=str(e), correlation_id=correlation_id)
        return JudgmentResult(
            verdict="WARN",
            grounding_score=50,
            issues=[{"type": "unsupported_claim", "ticker": None,
                     "detail": f"Errore judge: {e}"}],
            summary=f"Valutazione non completata ({e}). Pipeline continua in modalità degradata.",
        )
