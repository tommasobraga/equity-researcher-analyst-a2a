"""Demo mode support — returns pre-canned responses when DEMO_MODE=true.

No LLM calls are made. Enables end-to-end pipeline development without
cloud provider credentials. Not a workaround: no data leaves the local
perimeter.
"""
import json
from pathlib import Path


def load_demo_response(agent_name: str) -> dict:
    """Load the static demo response for the given agent.

    Args:
        agent_name: directory name under agents/, e.g. "data-collector"

    Returns:
        Dict with keys "message" (str) and "data" (dict).
    """
    path = Path(__file__).parent.parent / "agents" / agent_name / "demo" / "response.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def is_demo_mode() -> bool:
    import os
    return os.getenv("DEMO_MODE", "").lower() == "true"
