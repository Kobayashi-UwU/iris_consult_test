import pytest
from sqlalchemy import select

from ai.agents import criteria_core
from ai.client import input_hash
from core.db import session_scope
from core.models import AuditLog
from core.pipeline import ensure_interview_kit, ensure_invite, run_screening, send_invite, send_regrets
from core.workflow import (ROLE_HM, ROLE_RECRUITER, WorkflowError, approve_profile, bulk_approve_proposed,
                           can_generate, can_screen, confirm_blockers, confirm_shortlist, evaluations,
                           record_decision, set_status, statuses)
from tests.stub_llm import DEFAULT_PROFILE


def _approve(s, role=ROLE_HM):
    p = {**DEFAULT_PROFILE, "job_id": "GEP-2027"}
    return approve_profile(s, p, input_hash(criteria_core(p)), role, "test")


def test_full_flow_with_gates(fresh_db, stub_client):
    with session_scope() as s:
        assert not can_screen(s)[0]                       # gate 1 closed
        with pytest.raises(WorkflowError):
            _approve(s, ROLE_RECRUITER)                   # only HM approves
        _approve(s)
    with session_scope() as s:
        summary = run_screening(s, stub_client)
        assert summary["buckets"]["Ineligible"] == 1
        evs = evaluations(s)
        assert "suspicious_content" in evs["C-010"].flags
        assert evs["C-010"].bucket == "Needs Review"
        assert not can_generate(s)[0]                     # gate 2 closed
    with session_scope() as s:
        with pytest.raises(WorkflowError):
            confirm_shortlist(s, ROLE_RECRUITER)          # undecided candidates block confirmation
        bulk_approve_proposed(s, ROLE_RECRUITER)
        evs = evaluations(s)
        review = [cid for cid, e in evs.items() if e.bucket == "Needs Review"]
        with pytest.raises(WorkflowError):
            record_decision(s, review[0], "Approved", "", "", ROLE_RECRUITER)   # reason required
        for cid in review:
            record_decision(s, cid, "Rejected", "Evidence weaker than the score suggests",
                            "Checked the CV manually; evidence is thin.", ROLE_RECRUITER)
        notp = next(cid for cid, e in evs.items() if e.bucket == "Not Proposed")
        with pytest.raises(WorkflowError):
            record_decision(s, notp, "Approved", "", "short", ROLE_RECRUITER)   # override needs reason
        d = record_decision(s, notp, "Approved", "Evidence stronger than the score suggests",
                            "Strong practical project not captured by keywords.", ROLE_RECRUITER)
        assert d.is_override
        assert confirm_blockers(s) == []
        summary = confirm_shortlist(s, ROLE_RECRUITER)
        assert summary["overrides"] == 1
    with session_scope() as s:
        assert can_generate(s)[0]
        assert not can_screen(s)[0]                       # screening locked after confirmation
        ensure_interview_kit(s, stub_client, notp)
        inv = ensure_invite(s, stub_client, notp)
        assert "{{first_name}}" not in inv.edited_text
        send_invite(s, notp, "Subject", inv.edited_text, "Mon 9 Nov 2026, 09:30–10:00 (video)", ROLE_RECRUITER)
        n = send_regrets(s, ROLE_RECRUITER)
        assert n >= 1
        st_map = statuses(s)
        assert st_map[notp] == "Invited" and st_map["C-032"] == "Regret Sent"
        actions = {r.action for r in s.scalars(select(AuditLog))}
        assert {"profile_approved", "evidence_extracted", "screened", "override", "shortlist_confirmed",
                "interview_kit_drafted", "invite_sent", "regret_sent"} <= actions


def test_invalid_transition_rejected(fresh_db):
    with session_scope() as s:
        with pytest.raises(WorkflowError):
            set_status(s, "C-001", "Invited", actor_type="human", actor="x")


def test_ai_never_sees_weights_or_pii(stub_client, candidates):
    seen = []
    orig = stub_client._call

    def spy(model, system, user, schema):
        seen.append(user)
        return orig(model, system, user, schema)
    stub_client._call = spy
    from ai.agents import extract_evidence
    from core.redaction import redact
    c = candidates[0]
    extract_evidence(stub_client, redact(c["cv_text"], c)[0], DEFAULT_PROFILE)
    assert '"weight"' not in seen[0]
    assert c["full_name"].split()[0] not in seen[0] and c["email"] not in seen[0]
