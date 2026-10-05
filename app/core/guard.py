"""Rule-based detection of instructions aimed at the screening system.

Defence in depth: the AI is told to treat the CV as data and to flag manipulation,
and this independent check runs on the raw text regardless of what the AI says.
"""
import re

_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts?|rules)",
    r"disregard (all |any )?(the )?(previous|prior|above) ",
    r"(automated|ai|applicant tracking) (screening )?(system|screener|model)s?\b.{0,80}\b(rate|score|rank|select|shortlist)",
    r"\b(rate|score|rank)\b.{0,40}\b(this candidate|me)\b.{0,40}\b(maximum|highest|top|level 3|10/10|100)",
    r"system prompt",
    r"you are (now )?(an?|the) ",
]
_RX = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _PATTERNS]


def detect_injection(text: str) -> list[str]:
    hits = []
    for rx in _RX:
        m = rx.search(text)
        if m:
            hits.append(m.group(0)[:120])
    return hits
