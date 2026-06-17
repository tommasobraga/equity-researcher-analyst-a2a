"""Input sanitization — difesa da prompt injection su contenuti RSS non fidati.

Applicare a qualsiasi testo proveniente da fonti esterne prima che
raggiunga un prompt LLM.
"""
import re
import unicodedata

import bleach

_MAX_TITLE_LEN = 200
_MAX_SUMMARY_LEN = 500

# Caratteri di controllo Unicode (eccetto newline e tab normali)
_CONTROL_CHARS_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f"   # C0 controls (esclusi \t=0x09, \n=0x0a)
    r"\x80-\x9f"                            # C1 controls
    r"​-‏‪-‮﻿]",  # zero-width, bidi overrides, BOM
)

# Pattern di prompt injection comuni nei feed RSS malevoli
_INJECTION_PATTERNS_RE = re.compile(
    r"(ignore\s+(previous|prior|all)\s+instructions?|"
    r"system\s*:|<\s*/?system\s*>|"
    r"\[INST\]|\[/INST\]|"
    r"<\|im_start\|>|<\|im_end\|>)",
    re.IGNORECASE,
)


def _strip_html(text: str) -> str:
    return bleach.clean(text, tags=[], strip=True).strip()


def _remove_control_chars(text: str) -> str:
    text = _CONTROL_CHARS_RE.sub(" ", text)
    # Normalizza anche caratteri Unicode omoglifi e varianti di spazio
    return unicodedata.normalize("NFKC", text)


def _redact_injections(text: str) -> str:
    return _INJECTION_PATTERNS_RE.sub("[REDACTED]", text)


def sanitize_rss_item(title: str, summary: str) -> tuple[str, str]:
    """Sanitize title and summary from an RSS feed entry.

    Applies in order: HTML strip → control char removal → injection redaction → truncation.

    Returns:
        (clean_title, clean_summary) tuple, both safe to include in LLM prompts.
    """
    def clean(text: str, max_len: int) -> str:
        text = _strip_html(text or "")
        text = _remove_control_chars(text)
        text = _redact_injections(text)
        return text[:max_len].strip()

    return clean(title, _MAX_TITLE_LEN), clean(summary, _MAX_SUMMARY_LEN)
