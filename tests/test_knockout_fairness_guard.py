import pandas as pd

from core.fairness import impact_table
from core.guard import detect_injection
from core.knockout import check
from tests.stub_llm import DEFAULT_PROFILE


def test_only_planted_ineligible_candidate_is_knocked_out(candidates):
    failed = {c["candidate_id"]: check(c, DEFAULT_PROFILE["knockouts"]) for c in candidates}
    out = {cid for cid, (ok, _) in failed.items() if not ok}
    assert out == {"C-032"}
    assert "2022" in failed["C-032"][1][0]


def test_disabled_rule_is_skipped(candidates):
    rules = [{**r, "enabled": r["id"] != "graduation_window"} for r in DEFAULT_PROFILE["knockouts"]]
    assert all(check(c, rules)[0] for c in candidates)


def test_gpa_rule():
    rule = [{"id": "min_gpa", "field": "gpa", "operator": "gte", "values": ["3.00"], "description": "GPA ≥ 3"}]
    assert check({"gpa": 3.0}, rule)[0] and not check({"gpa": 2.99}, rule)[0]


def test_impact_ratio_known_case():
    df = pd.DataFrame({"g": ["A"] * 10 + ["B"] * 10, "sel": [True] * 5 + [False] * 5 + [True] * 2 + [False] * 8})
    t = impact_table(df, "g", "sel").set_index("group")
    assert t.loc["A", "impact_ratio"] == 1.0
    assert t.loc["B", "impact_ratio"] == 0.4
    assert t.loc["B", "status"].startswith("Below 0.8")


def test_injection_detected_only_in_planted_cv(candidates):
    hits = {c["candidate_id"] for c in candidates if detect_injection(c["cv_text"])}
    assert hits == {"C-010"}
