"""Input sanitization — defense against prompt injection from untrusted RSS content.

Apply to any text from external sources before it reaches an LLM prompt.
"""
import re
import unicodedata

import bleach

_MAX_TITLE_LEN = 200
_MAX_SUMMARY_LEN = 500

# Unicode control characters (except normal newline and tab)
_CONTROL_CHARS_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f"   # C0 controls (excluding \t=0x09, \n=0x0a)
    r"\x80-\x9f"                            # C1 controls
    r"​-‏‪-‮﻿]",  # zero-width, bidi overrides, BOM
)

# Cyrillic characters visually identical to Latin — used in homoglyph attacks on keywords
# like "system:". Note: у → y (not u) because Cyrillic у looks like Latin y.
_CYRILLIC_LOOKALIKES = str.maketrans({
    'а': 'a', 'е': 'e', 'о': 'o', 'р': 'p',
    'с': 'c', 'х': 'x', 'і': 'i', 'ѕ': 's', 'у': 'y',
})

# Base64-looking chunks have no legitimate use in financial news titles or summaries.
# Lookaheads require mixed case (real base64 always has both) to avoid false positives
# on repetitive strings like "AAAA..." used in truncation tests or CUSIPs/ISINs.
_BASE64_RE = re.compile(
    r"(?=[A-Za-z0-9+/]*[a-z])(?=[A-Za-z0-9+/]*[A-Z])[A-Za-z0-9+/]{20,}={0,2}"
)

# Common prompt injection patterns found in malicious RSS feeds.
# Syntactic patterns catch known tokens; semantic patterns catch common persona-hijack phrases.
# A denylist is inherently incomplete — structural separation in the prompt (XML tags) is the
# primary defense; this is the first cheap filter layer.
_INJECTION_PATTERNS_RE = re.compile(
    r"(ignore\s+(previous|prior|all)\s+instructions?|"
    r"system\s*:|<\s*/?system\s*>|"
    r"\[INST\]|\[/INST\]|"
    r"<\|im_start\|>|<\|im_end\|>|"
    r"act\s+as|"
    r"pretend\s+(you\s+are|to\s+be)|"
    r"you\s+are\s+now|"
    r"new\s+system\s+instructions?)",
    re.IGNORECASE,
)


def _strip_html(text: str) -> str:
    return bleach.clean(text, tags=[], strip=True).strip()


def _remove_control_chars(text: str) -> str:
    text = _CONTROL_CHARS_RE.sub(" ", text)
    # Also normalize Unicode homoglyphs and space variants
    return unicodedata.normalize("NFKC", text)


def _redact_injections(text: str) -> str:
    text = _BASE64_RE.sub("[REDACTED]", text)
    return _INJECTION_PATTERNS_RE.sub("[REDACTED]", text)


def sanitize_rss_item(title: str, summary: str) -> tuple[str, str]:
    """Sanitize title and summary from an RSS feed entry.

    Applies in order:
      1. Cross-field split injection check on raw combined text
      2. Per-field: injection redaction → HTML strip → control char removal → truncation

    Returns:
        (clean_title, clean_summary) tuple, both safe to include in LLM prompts.
    """
    # Normalize before any pattern matching: NFKC + Cyrillic lookalike substitution
    # so that "ѕуѕtеm:" is seen as "system:" by _INJECTION_PATTERNS_RE.
    def _normalize(text: str) -> str:
        return unicodedata.normalize("NFKC", text or "").translate(_CYRILLIC_LOOKALIKES)

    title_raw = _normalize(title)
    summary_raw = _normalize(summary)

    # Split injection: neither field alone triggers, but the concatenation does.
    # Redact both fields to avoid passing any fragment of the injection downstream.
    combined = f"{title_raw} {summary_raw}"
    if (
        _INJECTION_PATTERNS_RE.search(combined)
        and not _INJECTION_PATTERNS_RE.search(title_raw)
        and not _INJECTION_PATTERNS_RE.search(summary_raw)
    ):
        title_raw = "[REDACTED]"
        summary_raw = "[REDACTED]"

    def clean(text: str, max_len: int) -> str:
        text = _redact_injections(text)  # before HTML strip — catches <system> tags
        text = _strip_html(text)
        text = _remove_control_chars(text)
        return text[:max_len].strip()

    return clean(title_raw, _MAX_TITLE_LEN), clean(summary_raw, _MAX_SUMMARY_LEN)
