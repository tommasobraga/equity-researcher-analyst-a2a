"""Audit trail — append-only JSONL per ogni run della pipeline.

Ogni evento include correlation_id, agent, model_id, duration_ms,
status, prompt_hash, input_hash, output_hash e token_usage opzionale.

Il prompt_hash (SHA-256 del system prompt) traccia silenziosamente
le modifiche ai prompt hardcoded senza versioning esplicito.
"""
import hashlib
import json
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_OUTPUT_DIR = Path(__file__).parent.parent / "output"


def hash_content(content: str) -> str:
    """SHA-256 hex digest of a string. Used for prompt/input/output hashing."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def write_audit_event(event: dict[str, Any]) -> None:
    """Append one audit event to output/audit_{today}.jsonl.

    Always adds a UTC timestamp. Thread-safe within the same process.
    For multi-process deployments (one FastAPI process per agent) the
    OS-level append is atomic for small writes on local filesystems;
    use a proper log aggregator (e.g. Loki) in production.
    """
    _OUTPUT_DIR.mkdir(exist_ok=True)
    log_path = _OUTPUT_DIR / f"audit_{date.today().isoformat()}.jsonl"

    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
    line = json.dumps(record, ensure_ascii=False)

    with _lock:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def make_audit_event(
    *,
    agent: str,
    status: str,
    correlation_id: str | None = None,
    model_id: str | None = None,
    duration_ms: int | None = None,
    prompt: str | None = None,
    input_text: str | None = None,
    output_text: str | None = None,
    token_usage: dict[str, int] | None = None,
    demo_mode: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a well-structured audit event dict ready for write_audit_event().

    Args:
        agent:          Agent name, e.g. "DataCollector"
        status:         "completed" | "failed" | "demo"
        correlation_id: run_id propagated from the orchestrator
        model_id:       LLM model used, e.g. "claude-haiku-4-5-20251001"
        duration_ms:    Wall-clock time of the agent execution
        prompt:         System prompt text — hashed, never stored in clear
        input_text:     Serialized input — hashed, never stored in clear
        output_text:    Serialized output — hashed, never stored in clear
        token_usage:    {"input": N, "output": N} from the LLM response
        demo_mode:      True when DEMO_MODE=true, no LLM call was made
        extra:          Any additional fields to merge into the event
    """
    event: dict[str, Any] = {"agent": agent, "status": status}

    if correlation_id:
        event["correlation_id"] = correlation_id
    if model_id:
        event["model_id"] = model_id
    if duration_ms is not None:
        event["duration_ms"] = duration_ms
    if prompt is not None:
        event["prompt_hash"] = hash_content(prompt)
    if input_text is not None:
        event["input_hash"] = hash_content(input_text)
    if output_text is not None:
        event["output_hash"] = hash_content(output_text)
    if token_usage:
        event["token_usage"] = token_usage
    event["demo_mode"] = demo_mode
    if extra:
        event.update(extra)

    return event
