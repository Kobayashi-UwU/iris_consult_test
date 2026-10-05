"""Deterministic PII redaction. Runs before any CV text reaches the AI.

Two layers:
1. Replace values we already know from the structured application form
   (name, email, phone, date of birth, university and its aliases).
2. Pattern rules for anything else that identifies a person or a protected
   characteristic (contact lines, personal-details lines, titles, pronouns).
"""
import re
from datetime import datetime

UNIVERSITY_ALIASES: dict[str, list[str]] = {
    "Chulalongkorn University": ["Chulalongkorn", "Chula"],
    "King Mongkut's University of Technology Thonburi": ["KMUTT"],
    "King Mongkut's Institute of Technology Ladkrabang": ["KMITL", "Ladkrabang"],
    "King Mongkut's University of Technology North Bangkok": ["KMUTNB"],
    "Kasetsart University": ["Kasetsart"],
    "Mahidol University": ["Mahidol"],
    "Thammasat University": ["Thammasat", "Sirindhorn International Institute of Technology", "SIIT"],
    "Burapha University": ["Burapha"],
    "Srinakharinwirot University": ["Srinakharinwirot", "SWU"],
    "Chiang Mai University": ["CMU"],
    "Khon Kaen University": ["KKU"],
    "Suranaree University of Technology": ["Suranaree", "SUT"],
    "Ubon Ratchathani University": ["UBU"],
    "Naresuan University": ["Naresuan"],
    "Mahasarakham University": ["Mahasarakham", "MSU"],
    "Rajamangala University of Technology Isan": ["RMUTI"],
    "Rajamangala University of Technology Lanna": ["RMUTL"],
    "Rajamangala University of Technology Srivijaya": ["RMUTSV"],
}

# Lines that are entirely personal details in Thai-style CVs.
_PERSONAL_LINE = re.compile(
    r"^\s*[-•*]?\s*(date of birth|d\.o\.b\.?|dob|birth ?date|born|age|gender|sex|nationality|"
    r"religion|marital status|address|home address|hometown|height|weight|military status)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(\+66[\s-]?\d{1,2}|0\d{1,2})[\s-]?\d{3}[\s-]?\d{3,4}")
_TITLES = re.compile(r"\b(Mr|Mrs|Ms|Miss)\.?\s+(?=[A-Z])|นางสาว|นาย|นาง")
_PRONOUNS = re.compile(r"\b(he|she|him|her|his|hers|himself|herself)\b", re.IGNORECASE)
_GENDER_TERMS = re.compile(r"\b(women|woman|female|male|men|ladies|girls?|boys?)('s)?\b", re.IGNORECASE)
_LINKEDIN = re.compile(r"(https?://)?(www\.)?linkedin\.com/\S+", re.IGNORECASE)


def _apostrophe_variants(s: str) -> list[str]:
    return list({s, s.replace("'", "’"), s.replace("’", "'")})


def _date_variants(iso: str) -> list[str]:
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
    except ValueError:
        return [iso]
    return [
        iso,
        d.strftime("%d/%m/%Y"),
        f"{d.day}/{d.month}/{d.year}",
        f"{d.day} {d.strftime('%B')} {d.year}",
        f"{d.day:02d} {d.strftime('%B')} {d.year}",
        f"{d.day} {d.strftime('%b')} {d.year}",
        f"{d.strftime('%B')} {d.day}, {d.year}",
    ]


def _sub_literal(text: str, needle: str, token: str, word: bool = True) -> tuple[str, int]:
    if not needle:
        return text, 0
    pat = re.escape(needle)
    if word:
        pat = rf"(?<![\w]){pat}(?![\w])"
    return re.subn(pat, token, text, flags=re.IGNORECASE)


def redact(cv_text: str, cand: dict) -> tuple[str, dict[str, int]]:
    """Return (redacted_text, counts per redaction category)."""
    text = cv_text
    counts: dict[str, int] = {}

    def bump(cat: str, n: int) -> None:
        if n:
            counts[cat] = counts.get(cat, 0) + n

    # Whole lines first: contact lines and personal-details lines.
    lines = []
    for line in text.splitlines():
        if _EMAIL.search(line) or _PHONE.search(line) or (cand.get("email") and cand["email"] in line):
            lines.append("[CONTACT DETAILS REDACTED]")
            bump("contact", 1)
        else:
            lines.append(line)
    text = "\n".join(lines)
    text, n = _PERSONAL_LINE.subn("[PERSONAL DETAIL REDACTED]", text)
    bump("personal_details", n)

    # Known values from the application form.
    name = cand.get("full_name", "")
    text, n = _sub_literal(text, name, "[CANDIDATE]")
    bump("name", n)
    for part in name.split():
        if len(part) >= 3:
            text, n = _sub_literal(text, part, "[CANDIDATE]")
            bump("name", n)
    for variant in _date_variants(cand.get("date_of_birth", "")):
        text, n = _sub_literal(text, variant, "[DOB]")
        bump("date_of_birth", n)

    # All known universities (longest names first so aliases don't split them).
    names = []
    for full, aliases in UNIVERSITY_ALIASES.items():
        names += _apostrophe_variants(full) + aliases
    if cand.get("university"):
        names += _apostrophe_variants(cand["university"])
    for uni in sorted(set(names), key=len, reverse=True):
        text, n = _sub_literal(text, uni, "[UNIVERSITY]")
        bump("university", n)

    text, n = _LINKEDIN.subn("[URL]", text)
    bump("contact", n)
    text, n = _EMAIL.subn("[EMAIL]", text)
    bump("contact", n)
    text, n = _PHONE.subn("[PHONE]", text)
    bump("contact", n)
    text, n = _TITLES.subn("", text)
    bump("title", n)
    text, n = _PRONOUNS.subn("[PRONOUN]", text)
    bump("pronoun", n)
    text, n = _GENDER_TERMS.subn("[GENDER TERM]", text)
    bump("gender_term", n)

    # Collapse repeated tokens like "[CANDIDATE] [CANDIDATE]".
    text = re.sub(r"(\[CANDIDATE\])(\s+\[CANDIDATE\])+", r"\1", text)
    return text, counts


def pii_leaks(redacted: str, cand: dict) -> list[str]:
    """Known identifying values still present after redaction (should always be empty)."""
    leaks = []
    low = redacted.lower()
    for part in cand.get("full_name", "").split():
        if len(part) >= 3 and re.search(rf"(?<!\w){re.escape(part.lower())}(?!\w)", low):
            leaks.append(f"name:{part}")
    for key in ("email", "phone", "university"):
        v = str(cand.get(key, "")).lower()
        if v and v in low:
            leaks.append(key)
    for v in _date_variants(cand.get("date_of_birth", "")):
        if v.lower() in low:
            leaks.append("date_of_birth")
            break
    return leaks
