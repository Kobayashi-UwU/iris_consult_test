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

ui.header("Success profile", "The AI drafts job-related criteria from the job description. The Hiring Manager adjusts "
          "and approves them. Nothing is screened before this approval.", step=1)

live = st.session_state["mode"] == "live"

with session_scope() as s:
    job = s.get(Job, config.JOB_ID)
    prof = active_profile(s)
    history = [(r.version, r.status, r.approved_by, r.approved_at) for r in
               s.scalars(select(SuccessProfileRow).order_by(SuccessProfileRow.version))]
    confirmed = is_confirmed(s)
    cands = [{c.name: getattr(r, c.name) for c in Candidate.__table__.columns} for r in s.scalars(select(Candidate))]
    tiers = {d.candidate_id: d.university_tier for d in s.scalars(select(CandidateDemographic))}

TEXT_KEYS = ("def_", "name_", "a0_", "a1_", "a2_", "a3_", "ko_", "gpa_", "weights_editor")


def load_draft(profile: dict, meta: dict) -> None:
    for k in list(st.session_state.keys()):
        if k.startswith(TEXT_KEYS):
            del st.session_state[k]
    st.session_state["draft"] = copy.deepcopy(profile)
    st.session_state["draft_meta"] = meta


if "draft" not in st.session_state and prof:
    load_draft(prof.profile_json, {"source": f"approved v{prof.version}"})

has_draft = "draft" in st.session_state
with st.expander("Job description", expanded=not has_draft):
    jd = st.text_area("Job description", value=st.session_state.get("jd", job.jd_text), height=300,
                      disabled=not live, label_visibility="collapsed")
    st.session_state["jd"] = jd
    if not live:
        st.caption("Editable in Live AI mode.")

left, right = st.columns([0.3, 0.7], vertical_alignment="center")
if left.button("Draft with AI", type="secondary" if has_draft else "primary", disabled=confirmed,
               width="stretch"):
    with st.spinner("Reading the job description"):
        try:
            draft, res = agents.build_profile(ui.client(), jd)
            load_draft(draft, {"source": res.source, "model": res.model, "prompt_version": res.prompt_version})
            st.rerun()
        except LLMUnavailable as exc:
            ui.note(ui.esc(exc), "bad")
if "draft_meta" in st.session_state:
    m = st.session_state["draft_meta"]
    right.caption(f"Loaded from {m.get('source')}" + (f" · {m['model']} · {m['prompt_version']}" if m.get("model") else ""))

if not has_draft:
    st.stop()

draft = st.session_state["draft"]
if draft.get("role_summary"):
    ui.note(ui.esc(draft["role_summary"]), "plain")

# ---------------- criteria
st.markdown("## Scored criteria")
st.caption("The AI rates each criterion from 0 to 3 against its anchors. Code multiplies by the weight; "
           "the AI never sees weights." + ("" if live else " In Demo mode only weights and knock-outs can change."))

wdf = pd.DataFrame([{"Criterion": c["name"], "Weight": int(c["weight"])} for c in draft["criteria"]])
edited = st.data_editor(
    wdf, key="weights_editor", hide_index=True, width="stretch", disabled=["Criterion"],
    column_config={"Weight": st.column_config.NumberColumn(min_value=0, max_value=100, step=5, width="small")},
)
for c, w in zip(draft["criteria"], edited["Weight"].tolist()):
    c["weight"] = int(w or 0)
total = sum(c["weight"] for c in draft["criteria"])
st.caption(f"Total weight: **{total}** / 100")

with st.expander("Definitions and anchors"):
    for c in draft["criteria"]:
        cid = c["id"]
        st.markdown(f"**{c['name']}**")
        if live:
            c["definition"] = st.text_area("Definition", c["definition"], key=f"def_{cid}", height=70)
            for i in range(4):
                c["anchors"][f"level_{i}"] = st.text_input(f"Level {i}", c["anchors"][f"level_{i}"], key=f"a{i}_{cid}")
        else:
            st.markdown(f"<div class='muted'>{ui.esc(c['definition'])}</div>", unsafe_allow_html=True)
            st.markdown("\n".join(f"- **{i}** · {c['anchors'][f'level_{i}']}" for i in range(4)))
        st.write("")

# ---------------- knock-outs
st.markdown("## Eligibility")
st.caption("Checked by rules on the application form, not by the AI. Anyone who fails gets a clear reason.")
for k in draft["knockouts"]:
    if k["id"] == "min_gpa":
        continue
    k["enabled"] = st.checkbox(k["description"], value=k.get("enabled", True), key=f"ko_{k['id']}")

gpa_rule = next((k for k in draft["knockouts"] if k["id"] == "min_gpa"), None)
use_gpa = st.checkbox("Minimum GPA (not in the job description)", value=bool(gpa_rule and gpa_rule.get("enabled")),
                      key="gpa_on")
if use_gpa:
    gmin = st.slider("Minimum GPA", 2.0, 3.5, float(gpa_rule["values"][0]) if gpa_rule else 2.75, 0.05, key="gpa_val")
    rule = {"id": "min_gpa", "field": "gpa", "operator": "gte", "values": [f"{gmin:.2f}"],
            "description": f"GPA of at least {gmin:.2f}", "enabled": True}
    if gpa_rule:
        gpa_rule.update(rule)
    else:
        draft["knockouts"].append(rule)
    cut = [c for c in cands if not knockout.check_rule(c, rule)[0]]
    by_tier = pd.Series([tiers[c["candidate_id"]] for c in cut]).value_counts().to_dict() if cut else {}
    all_tier = pd.Series(list(tiers.values())).value_counts().to_dict()
    ui.note(
        f"This removes <b>{len(cut)} of {len(cands)}</b> applicants before anyone reads their CV"
        + (" (" + ", ".join(f"{t}: {by_tier.get(t, 0)}/{all_tier[t]}" for t in sorted(all_tier)) + ")" if cut else "")
        + ". GPA often reflects circumstances, such as working while studying, as much as ability.", "warn")
elif gpa_rule:
    gpa_rule["enabled"] = False

# ---------------- approve (HITL 1)
st.markdown("## Approval")
errors = validate_profile(draft)
is_hm = ui.role() == ROLE_HM
for e in errors:
    ui.note(ui.esc(e), "bad")
if not is_hm:
    ui.note("Only the <b>Hiring Manager</b> can approve. Switch role in the sidebar.", "plain")
elif prof:
    st.caption(f"v{prof.version} is approved. Approving again creates v{prof.version + 1} and asks for screening to be re-run.")
if st.button("Approve success profile", type="primary", disabled=bool(errors) or not is_hm or confirmed):
    try:
        with session_scope() as s:
            to_save = copy.deepcopy(draft)
            to_save["job_id"] = config.JOB_ID
            row = approve_profile(s, to_save, input_hash(agents.criteria_core(to_save)), ui.role(),
                                  st.session_state.get("draft_meta", {}).get("source", ""))
            v = row.version
        st.session_state["draft_meta"] = {"source": f"approved v{v}"}
        ui.note(f"Version {v} approved.", "good")
        st.page_link("views/screening.py", label="Continue to screening")
    except WorkflowError as exc:
        ui.note(ui.esc(exc), "bad")

if history:
    with st.expander("Version history"):
        st.dataframe(pd.DataFrame(history, columns=["Version", "Status", "Approved by", "Approved at"]),
                     hide_index=True, width="stretch")
