from core.scoring import assign_buckets, enrich_assessments, quote_found, rescore, total_score

CRITERIA = [
    {"id": "a", "name": "A", "weight": 60},
    {"id": "b", "name": "B", "weight": 40},
]


def test_score_formula_and_determinism():
    ass = [{"criterion_id": "a", "level": 3, "evidence_quotes": ["led a team"], "rationale": "", "gaps": []},
           {"criterion_id": "b", "level": 1, "evidence_quotes": ["python"], "rationale": "", "gaps": []}]
    cv = "I led a team of four. Skills: Python."
    e1, f1 = enrich_assessments(ass, CRITERIA, cv)
    e2, _ = enrich_assessments(ass, CRITERIA, cv)
    assert total_score(e1) == total_score(e2) == round(60 + 40 / 3, 1)
    assert f1 == []


def test_unverified_quote_and_missing_criterion_flagged():
    ass = [{"criterion_id": "a", "level": 2, "evidence_quotes": ["won a Nobel prize"], "rationale": "", "gaps": []}]
    enriched, flags = enrich_assessments(ass, CRITERIA, "I led a team.")
    assert "unverified_quote" in flags and "missing_criterion" in flags
    assert enriched[1]["level"] == 0


def test_level_without_quote_flagged():
    ass = [{"criterion_id": "a", "level": 2, "evidence_quotes": [], "rationale": "", "gaps": []},
           {"criterion_id": "b", "level": 0, "evidence_quotes": [], "rationale": "", "gaps": []}]
    _, flags = enrich_assessments(ass, CRITERIA, "text")
    assert flags == ["unsupported_level"]


def test_quote_match_tolerates_whitespace_and_quotes():
    assert quote_found("reduced  fuel use by about 8%", "…we reduced fuel\nuse by about 8% over 10 weeks")
    assert not quote_found("increased revenue tenfold", "reduced fuel use by about 8%")


def test_rescore_applies_new_weights():
    ass = [{"criterion_id": "a", "level": 3, "points": 60, "weight": 60},
           {"criterion_id": "b", "level": 0, "points": 0, "weight": 40}]
    _, total = rescore(ass, [{"id": "a", "weight": 50}, {"id": "b", "weight": 50}])
    assert total == 50.0


def test_buckets():
    rows = [{"candidate_id": f"C{i}", "eligible": True, "score": 100 - i * 3, "flags": []} for i in range(10)]
    rows.append({"candidate_id": "X", "eligible": False, "score": 0, "flags": []})
    rows[1]["flags"] = ["suspicious_content"]
    b = assign_buckets(rows, shortlist_size=4, band=5)
    assert b["C0"] == ("Proposed", 1)
    assert b["C1"][0] == "Needs Review"          # flagged, even though top-ranked
    assert b["C4"][0] == "Needs Review"          # 88 is within 5 of the cut-off (91)
    assert b["C6"][0] == "Not Proposed"
    assert b["X"] == ("Ineligible", 0)
