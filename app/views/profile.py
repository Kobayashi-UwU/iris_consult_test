import copy

import pandas as pd
import streamlit as st
from sqlalchemy import select

import ui
from ai import agents
from ai.client import LLMUnavailable, input_hash
from core import config, knockout
from core.db import session_scope
from core.models import Candidate, CandidateDemographic, Job, SuccessProfileRow
from core.workflow import ROLE_HM, WorkflowError, active_profile, approve_profile, is_confirmed, validate_profile

ui.header("Success profile", "Agree what a strong graduate looks like before anyone is screened. The AI drafts it "
          "from the job description; the Hiring Manager approves it.", step=1)

live = st.session_state["mode"] == "live"

with session_scope() as s:
    job = s.get(Job, config.JOB_ID)
    prof = active_profile(s)
    history = [(r.version, r.status, r.approved_by, r.approved_at) for r in
               s.scalars(select(SuccessProfileRow).order_by(SuccessProfileRow.version))]
    confirmed = is_confirmed(s)
    cands = [{c.name: getattr(r, c.name) for c in Candidate.__table__.columns} for r in s.scalars(select(Candidate))]
    tiers = {d.candidate_id: d.university_tier for d in s.scalars(select(CandidateDemographic))}

WIDGET_KEYS = ("def_", "a0_", "a1_", "a2_", "a3_", "ko_", "gpa_", "weights_editor")


def load_draft(profile: dict, meta: dict) -> None:
    for k in list(st.session_state.keys()):
        if k.startswith(WIDGET_KEYS):
            del st.session_state[k]
    st.session_state["draft"] = copy.deepcopy(profile)
    st.session_state["draft_meta"] = meta


def generate() -> None:
    with st.spinner("The AI is reading the job description"):
        try:
            draft, res = agents.build_profile(ui.client(), st.session_state.get("jd", job.jd_text))
            load_draft(draft, {"source": res.source, "model": res.model})
            st.rerun()
        except LLMUnavailable as exc:
            ui.note(ui.esc(exc), "bad")


if "draft" not in st.session_state and prof:
    load_draft(prof.profile_json, {"source": f"approved v{prof.version}"})

# ---------------- no draft yet: one obvious action
if "draft" not in st.session_state:
    st.markdown("<div class='hero'><div class='big'>Let the AI draft the criteria</div><div class='muted'>It reads the "
                "job description and proposes 5 to 7 job-related criteria, how much each one matters, and the "
                "eligibility rules. You can adjust everything before approving.</div></div>", unsafe_allow_html=True)
    if st.button("Draft the success profile", type="primary"):
        generate()
    with st.expander("Read the job description"):
        st.markdown(job.jd_text)
    st.stop()

draft = st.session_state["draft"]


def signature(p: dict) -> tuple:
    return (tuple((c["id"], c["weight"], c["definition"], tuple(c["anchors"].values())) for c in p["criteria"]),
            tuple((k["id"], k.get("enabled", True), tuple(k["values"])) for k in p["knockouts"]))


# The summary and the approve button render above the optional adjustments, but are filled in after them,
# so they always reflect the latest edits.
top = st.container()

with st.expander("Adjust criteria and eligibility (optional)"):
    st.caption("Weights decide how much each criterion counts. The AI rates evidence from 0 to 3; code applies the "
               "weights, so the AI never sees them." + ("" if live else " In demo mode criterion wording is fixed."))
    wdf = pd.DataFrame([{"Criterion": c["name"], "Weight": int(c["weight"])} for c in draft["criteria"]])
    edited = st.data_editor(wdf, key="weights_editor", hide_index=True, width="stretch", disabled=["Criterion"],
                            column_config={"Weight": st.column_config.NumberColumn(min_value=0, max_value=100, step=5)})
    for c, w in zip(draft["criteria"], edited["Weight"].tolist()):
        c["weight"] = int(w or 0)
    st.markdown("**Eligibility rules**")
    for k in draft["knockouts"]:
        if k["id"] != "min_gpa":
            k["enabled"] = st.checkbox(k["description"], value=k.get("enabled", True), key=f"ko_{k['id']}")
    gpa_rule = next((k for k in draft["knockouts"] if k["id"] == "min_gpa"), None)
    if st.checkbox("Also require a minimum GPA (not in the job description)",
                   value=bool(gpa_rule and gpa_rule.get("enabled")), key="gpa_on"):
        gmin = st.slider("Minimum GPA", 2.0, 3.5, float(gpa_rule["values"][0]) if gpa_rule else 2.75, 0.05,
                         key="gpa_val")
        rule = {"id": "min_gpa", "field": "gpa", "operator": "gte", "values": [f"{gmin:.2f}"],
                "description": f"GPA of at least {gmin:.2f}", "enabled": True}
        if gpa_rule:
            gpa_rule.update(rule)
        else:
            draft["knockouts"].append(rule)
        cut = [c for c in cands if not knockout.check_rule(c, rule)[0]]
        by_tier = pd.Series([tiers[c["candidate_id"]] for c in cut]).value_counts().to_dict() if cut else {}
        all_tier = pd.Series(list(tiers.values())).value_counts().to_dict()
        ui.note(f"This removes <b>{len(cut)} of {len(cands)}</b> applicants before anyone reads their CV"
                + (" (" + ", ".join(f"{t}: {by_tier.get(t, 0)}/{all_tier[t]}" for t in sorted(all_tier)) + ")" if cut else "")
                + ". GPA often reflects circumstances, such as working while studying, as much as ability.", "warn")
    elif gpa_rule:
        gpa_rule["enabled"] = False
    if live:
        st.markdown("**Definitions and anchors**")
        for c in draft["criteria"]:
            c["definition"] = st.text_area(c["name"], c["definition"], key=f"def_{c['id']}", height=70)
            for i in range(4):
                c["anchors"][f"level_{i}"] = st.text_input(f"Level {i}", c["anchors"][f"level_{i}"],
                                                           key=f"a{i}_{c['id']}")
    if st.button("Redraft with AI", disabled=confirmed, key="redraft", help="Discard changes and ask the AI again."):
        generate()

with top:
    # ---------------- summary + the one action
    is_approved = bool(prof) and signature(prof.profile_json) == signature(draft)
    errors = validate_profile(draft)
    total = sum(c["weight"] for c in draft["criteria"])

    rows = "".join(
        f"<div style='display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid {ui.LINE}'>"
        f"<span>{ui.esc(c['name'])}</span><b>{c['weight']}%</b></div>" for c in draft["criteria"])
    rules = "".join(f"<li>{ui.esc(k['description'])}</li>" for k in draft["knockouts"] if k.get("enabled", True))
    st.markdown(
        f"<div class='hero'><div class='label muted'>What we will look for</div>"
        f"<div class='muted' style='margin:4px 0 10px 0'>{ui.esc(draft.get('role_summary', ''))}</div>{rows}"
        f"<div class='label muted' style='margin-top:14px'>Must have</div><ul style='margin:4px 0 0 18px'>{rules}</ul></div>",
        unsafe_allow_html=True)

    with st.expander("What each level means for each criterion"):
        for c in draft["criteria"]:
            st.markdown(f"**{c['name']}** · <span class='muted'>{ui.esc(c['definition'])}</span>", unsafe_allow_html=True)
            st.markdown("\n".join(f"- **{i}** · {c['anchors'][f'level_{i}']}" for i in range(4)))

    for e in errors:
        ui.note(ui.esc(e), "bad")

    if is_approved:
        ui.note(f"Approved by the Hiring Manager as version {prof.version}.", "good")
        ui.next_up(1)
    else:
        label = "Approve as Hiring Manager" if not prof else f"Approve changes as Hiring Manager (version {prof.version + 1})"
        if prof:
            ui.note("You changed the approved profile. Approving creates a new version and screening must run again.", "warn")
        if st.button(label, type="primary", disabled=bool(errors) or confirmed):
            try:
                with session_scope() as s:
                    to_save = copy.deepcopy(draft)
                    to_save["job_id"] = config.JOB_ID
                    approve_profile(s, to_save, input_hash(agents.criteria_core(to_save)), ROLE_HM,
                                    st.session_state.get("draft_meta", {}).get("source", ""))
                st.rerun()
            except WorkflowError as exc:
                ui.note(ui.esc(exc), "bad")
        st.caption(f"Weights total {total}%. Nothing is screened until this is approved.")


with st.expander("Job description and version history"):
    if live:
        st.session_state["jd"] = st.text_area("Job description", value=st.session_state.get("jd", job.jd_text),
                                              height=300)
    else:
        st.markdown(job.jd_text)
    if history:
        st.dataframe(pd.DataFrame(history, columns=["Version", "Status", "Approved by", "Approved at"]),
                     hide_index=True, width="stretch")
    m = st.session_state.get("draft_meta", {})
    if m.get("model"):
        st.caption(f"Drafted by {m['model']} ({m['source']})")
