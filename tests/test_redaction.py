import re

from core.redaction import pii_leaks, redact


def test_no_known_pii_survives(candidates):
    for c in candidates:
        red, _ = redact(c["cv_text"], c)
        assert pii_leaks(red, c) == [], c["candidate_id"]


def test_contact_and_personal_lines_removed(candidates):
    for c in candidates:
        red, _ = redact(c["cv_text"], c)
        assert "@" not in red, c["candidate_id"]
        assert not re.search(r"0\d{2}-\d{3}-\d{4}", red), c["candidate_id"]
        assert not re.search(r"(?im)^\s*(date of birth|gender|nationality)\s*:", red), c["candidate_id"]


def test_gender_terms_and_pronouns_removed():
    cand = {"full_name": "Test Person", "email": "t@example.com", "phone": "081-111-1111",
            "date_of_birth": "2003-01-02", "university": "Chiang Mai University"}
    cv = "Test Person\nLead, Women in Engineering Club. She led the team at Chiang Mai University (CMU)."
    red, counts = redact(cv, cand)
    for word in ("Women", "She", "Chiang Mai University", "CMU", "Test", "Person"):
        assert word not in red
    assert counts["university"] == 2


def test_redaction_keeps_job_evidence(candidates):
    c = next(x for x in candidates if x["candidate_id"] == "C-004")
    red, _ = redact(c["cv_text"], c)
    assert "8%" in red and "permit-to-work" in red.lower()
