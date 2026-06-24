"""
Harness Analyzer — offline LLM-powered analysis of pipeline execution traces.

Reads output/raw_*.json and output/audit_*.jsonl, aggregates patterns
(validator violations, judge issues, agent failures, degraded flags),
and calls Claude to produce a WeaknessReport with actionable harness fixes.

Usage:
  uv run python analysis/harness_analyzer.py              # last 20 runs
  uv run python analysis/harness_analyzer.py --last-n 10
  uv run python analysis/harness_analyzer.py --no-llm     # stats only, no LLM call
  uv run python analysis/harness_analyzer.py --output report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.demo import is_demo_mode
from shared.models import Report
from shared.validators import Violation, validate

_OUTPUT_DIR = _ROOT / "output"
_MODEL_ID = "claude-sonnet-4-6"


# ── Data loading ──────────────────────────────────────────────────────────────

@dataclass
class RunRecord:
    run_id: str
    timestamp: datetime
    mode: str
    demo: bool
    judgment: dict
    violations: list[Violation]
    qa_verdict: str
    degraded: dict
    agent_events: list[dict]


def _parse_report(raw: dict) -> Report | None:
    report_dict = raw.get("report")
    if not report_dict:
        return None
    try:
        return Report.model_validate(report_dict)
    except Exception:
        return None


def _load_audit_index(output_dir: Path) -> dict[str, list[dict]]:
    """Build correlation_id → [events] index from all audit JSONL files."""
    index: dict[str, list[dict]] = defaultdict(list)
    for path in output_dir.glob("audit_*.jsonl"):
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        cid = event.get("correlation_id")
                        if cid:
                            index[cid].append(event)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return dict(index)


def load_runs(output_dir: Path, last_n: int) -> list[RunRecord]:
    """Load the last N runs from raw_*.json, correlated with audit events."""
    raw_files = sorted(
        output_dir.glob("raw_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:last_n]

    if not raw_files:
        return []

    audit_index = _load_audit_index(output_dir)
    records: list[RunRecord] = []

    for path in raw_files:
        run_id = path.stem[4:]  # strip "raw_" prefix
        try:
            with path.open(encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        report_obj = _parse_report(raw)
        violations = validate(report_obj)

        agent_events = audit_index.get(run_id, [])
        demo = all(e.get("demo_mode", True) for e in agent_events) if agent_events else True

        timestamps = [
            datetime.fromisoformat(e["timestamp"])
            for e in agent_events
            if "timestamp" in e
        ]
        ts = min(timestamps) if timestamps else datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc
        )

        records.append(RunRecord(
            run_id=run_id,
            timestamp=ts,
            mode=raw.get("mode", "unknown"),
            demo=demo,
            judgment=raw.get("judgment", {}),
            violations=violations,
            qa_verdict=raw.get("qa_verdict", ""),
            degraded=raw.get("degraded", {}),
            agent_events=agent_events,
        ))

    return sorted(records, key=lambda r: r.timestamp)


# ── Aggregation ───────────────────────────────────────────────────────────────

@dataclass
class AgentStat:
    total: int = 0
    failed: int = 0
    demo: int = 0
    durations_ms: list[int] = field(default_factory=list)

    @property
    def avg_ms(self) -> int:
        return int(sum(self.durations_ms) / len(self.durations_ms)) if self.durations_ms else 0


@dataclass
class TracesSummary:
    runs_analyzed: int
    live_runs: int
    demo_runs: int
    date_range: tuple[str, str]
    violation_msgs: dict[str, list[str]]    # rule → all messages (count = len)
    judge_issue_msgs: dict[str, list[str]]  # issue_type → all details (count = len)
    agent_stats: dict[str, AgentStat]
    degraded_counts: dict[str, int]
    grounding_scores: list[int]
    modes: dict[str, int]


def aggregate_traces(records: list[RunRecord]) -> TracesSummary:
    violation_msgs: dict[str, list[str]] = defaultdict(list)
    judge_msgs: dict[str, list[str]] = defaultdict(list)
    agent_stats: dict[str, AgentStat] = defaultdict(AgentStat)
    degraded_map: dict[str, int] = defaultdict(int)
    scores: list[int] = []
    modes: dict[str, int] = defaultdict(int)

    for rec in records:
        modes[rec.mode] += 1

        for v in rec.violations:
            violation_msgs[v.rule].append(v.message)

        for issue in rec.judgment.get("issues", []):
            itype = issue.get("type", "unknown")
            judge_msgs[itype].append(issue.get("detail", ""))

        score = rec.judgment.get("grounding_score")
        if score is not None:
            scores.append(int(score))

        for key in rec.degraded:
            degraded_map[key] += 1

        for ev in rec.agent_events:
            agent = ev.get("agent", "unknown")
            stat = agent_stats[agent]
            stat.total += 1
            if ev.get("status") == "failed":
                stat.failed += 1
            if ev.get("demo_mode"):
                stat.demo += 1
            dur = ev.get("duration_ms")
            if dur is not None:
                stat.durations_ms.append(int(dur))

    live = sum(1 for r in records if not r.demo)
    demo_count = sum(1 for r in records if r.demo)

    dates = [r.timestamp.date().isoformat() for r in records]
    date_range = (min(dates), max(dates)) if dates else ("n/a", "n/a")

    return TracesSummary(
        runs_analyzed=len(records),
        live_runs=live,
        demo_runs=demo_count,
        date_range=date_range,
        violation_msgs=dict(violation_msgs),
        judge_issue_msgs=dict(judge_msgs),
        agent_stats=dict(agent_stats),
        degraded_counts=dict(degraded_map),
        grounding_scores=scores,
        modes=dict(modes),
    )


# ── Stats display ─────────────────────────────────────────────────────────────

def print_stats(summary: TracesSummary) -> None:
    n = summary.runs_analyzed
    print(f"\n=== HARNESS ANALYZER -- {n} runs "
          f"({summary.date_range[0]} -> {summary.date_range[1]}) ===")
    print(f"  Live: {summary.live_runs}  Demo: {summary.demo_runs}  "
          f"Modes: {dict(summary.modes)}")

    if summary.grounding_scores:
        avg = sum(summary.grounding_scores) / len(summary.grounding_scores)
        below = sum(1 for s in summary.grounding_scores if s < 60)
        print(f"\nGrounding scores  avg={avg:.1f}  "
              f"below_threshold={below}/{len(summary.grounding_scores)}")

    if summary.violation_msgs:
        print("\nValidator violations (by rule):")
        for rule, msgs in sorted(summary.violation_msgs.items(), key=lambda x: -len(x[1])):
            print(f"  {rule}: {len(msgs)} occurrence(s)")
            for msg in msgs[:2]:
                print(f"    · {msg}")
    else:
        print("\nValidator violations: none")

    if summary.judge_issue_msgs:
        print("\nLLM Judge issues (by type):")
        for itype, details in sorted(summary.judge_issue_msgs.items(), key=lambda x: -len(x[1])):
            print(f"  {itype}: {len(details)} occurrence(s)")
            for d in details[:2]:
                print(f"    · {d}")
    else:
        print("\nLLM Judge issues: none")

    if summary.degraded_counts:
        print("\nDegraded flags:")
        for key, count in sorted(summary.degraded_counts.items(), key=lambda x: -x[1]):
            print(f"  {key}: {count}")

    if summary.agent_stats:
        print("\nAgent execution:")
        for agent, stat in sorted(summary.agent_stats.items()):
            live_calls = stat.total - stat.demo
            print(f"  {agent}: total={stat.total}  failed={stat.failed}  "
                  f"demo={stat.demo}  live={live_calls}  avg_ms={stat.avg_ms}")
    print()


# ── LLM analysis ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a senior ML engineer analyzing execution traces of a multi-agent equity research pipeline.
The pipeline has 6 agents: DataCollector, NewsSentiment, FundamentalAnalyst, RiskAssessor, ReportWriter, PortfolioManager.
Each agent has a system prompt (its "harness") that can be improved to reduce systematic failures.

You receive aggregated execution statistics. Your task:
1. Identify SYSTEMATIC patterns — issues appearing in ≥2 runs or with a clear structural cause
2. Attribute each pattern to a specific agent's harness
3. Distinguish prompt failures (fixable by changing the system prompt) from data/tool failures (not fixable via prompt)
4. For each pattern propose a concrete fix: a new rule to add to the system prompt, or a few-shot example
5. If all runs are demo-only, note this limits inference about live LLM behavior

Respond ONLY with valid JSON (no markdown fences):
{
  "executive_summary": "2-3 sentences overall assessment",
  "patterns": [
    {
      "agent": "AgentName",
      "rule": "short_rule_id",
      "frequency": N,
      "rate": 0.0,
      "hypothesis": "causal explanation — why this happens structurally",
      "suggested_fix": "concrete prompt addition or few-shot example text",
      "confidence": "high|medium|low",
      "target": "system_prompt_rule|few_shot_example|tool_config|retry_logic"
    }
  ]
}"""


def _build_llm_prompt(summary: TracesSummary) -> str:
    n = summary.runs_analyzed
    lines = [
        f"=== TRACE SUMMARY ({n} runs, "
        f"{summary.date_range[0]} -> {summary.date_range[1]}) ===",
        f"Live: {summary.live_runs}  Demo: {summary.demo_runs}",
        f"Modes: {dict(summary.modes)}",
    ]

    if summary.grounding_scores:
        avg = sum(summary.grounding_scores) / len(summary.grounding_scores)
        below = sum(1 for s in summary.grounding_scores if s < 60)
        lines.append(
            f"\nGrounding scores: avg={avg:.1f}  "
            f"below_threshold={below}/{len(summary.grounding_scores)}"
        )
        lines.append(f"  All scores: {summary.grounding_scores}")

    if summary.violation_msgs:
        lines.append("\nValidator violations (rule: total_count, up to 3 examples):")
        for rule, msgs in sorted(summary.violation_msgs.items(), key=lambda x: -len(x[1])):
            lines.append(f"  {rule}: {len(msgs)} occurrences")
            for msg in msgs[:3]:
                lines.append(f"    - {msg}")
    else:
        lines.append("\nValidator violations: none")

    if summary.judge_issue_msgs:
        lines.append("\nLLM Judge issues (type: total_count, up to 3 examples):")
        for itype, details in sorted(summary.judge_issue_msgs.items(), key=lambda x: -len(x[1])):
            lines.append(f"  {itype}: {len(details)} occurrences")
            for d in details[:3]:
                lines.append(f"    - {d}")
    else:
        lines.append("\nLLM Judge issues: none")

    if summary.degraded_counts:
        lines.append("\nDegraded flags (count across runs):")
        for key, count in sorted(summary.degraded_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {key}: {count}")

    lines.append("\nAgent execution stats:")
    for agent, stat in sorted(summary.agent_stats.items()):
        lines.append(
            f"  {agent}: total={stat.total} failed={stat.failed} "
            f"demo={stat.demo} avg_ms={stat.avg_ms}"
        )

    if summary.demo_runs == summary.runs_analyzed:
        lines.append(
            "\nNOTE: ALL runs are demo runs (DEMO_MODE=true). "
            "Outputs are fixed — violation and judge patterns reflect the static demo "
            "responses, not live LLM behavior. Set confidence accordingly."
        )

    lines.append("\nAnalyze and return your JSON WeaknessReport.")
    return "\n".join(lines)


# ── Output models ─────────────────────────────────────────────────────────────

class WeaknessPattern(BaseModel):
    agent: str
    rule: str
    frequency: int
    rate: float
    hypothesis: str
    suggested_fix: str
    confidence: Literal["high", "medium", "low"]
    target: Literal["system_prompt_rule", "few_shot_example", "tool_config", "retry_logic"]


class WeaknessReport(BaseModel):
    generated_at: str
    runs_analyzed: int
    live_runs: int
    patterns: list[WeaknessPattern]
    executive_summary: str


def analyze_with_llm(summary: TracesSummary) -> WeaknessReport:
    from shared.llm_client import get_llm_client

    client = get_llm_client()
    user_prompt = _build_llm_prompt(summary)

    response = client.messages.create(
        model=_MODEL_ID,
        max_tokens=2048,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw = response.content[0].text.strip()

    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError(f"No JSON object in LLM response: {raw[:200]}")
    data = json.loads(raw[start:end])

    return WeaknessReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        runs_analyzed=summary.runs_analyzed,
        live_runs=summary.live_runs,
        patterns=[WeaknessPattern(**p) for p in data.get("patterns", [])],
        executive_summary=data.get("executive_summary", ""),
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze pipeline execution traces for systematic harness weaknesses."
    )
    parser.add_argument(
        "--last-n", type=int, default=20, metavar="N",
        help="Analyze last N pipeline runs (default: 20)",
    )
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Print aggregated stats only, skip LLM analysis",
    )
    parser.add_argument(
        "--output", metavar="PATH",
        help="Write WeaknessReport JSON to PATH (default: stdout)",
    )
    args = parser.parse_args()

    records = load_runs(_OUTPUT_DIR, args.last_n)
    if not records:
        print(
            "No run records found in output/. Run the pipeline first.",
            file=sys.stderr,
        )
        sys.exit(1)

    summary = aggregate_traces(records)
    print_stats(summary)

    if args.no_llm:
        return

    if is_demo_mode():
        print("DEMO_MODE=true — skipping LLM analysis call.", file=sys.stderr)
        return

    print("Running LLM analysis...", file=sys.stderr)
    try:
        report = analyze_with_llm(summary)
    except Exception as exc:
        print(f"LLM analysis failed: {exc}", file=sys.stderr)
        sys.exit(1)

    output_json = report.model_dump_json(indent=2)

    if args.output:
        Path(args.output).write_text(output_json, encoding="utf-8")
        print(f"WeaknessReport written to {args.output}", file=sys.stderr)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
