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

st.title("Step 1 · Success Profile")
st.caption("The AI drafts job-related criteria from the job description. The **Hiring Manager** edits and approves them. "
           "Nothing is screened until this approval exists.")
ui.stepper(1)

live = st.session_state["mode"] == "live"

with session_scope() as s:
    job = s.get(Job, config.JOB_ID)
    prof = active_profile(s)
    history = [(r.version, r.status, r.approved_by, r.approved_at) for r in
               s.scalars(select(SuccessProfileRow).order_by(SuccessProfileRow.version))]
    confirmed = is_confirmed(s)
    cands = [{c.name: getattr(r, c.name) for c in Candidate.__table__.columns} for r in s.scalars(select(Candidate))]
    demo = {d.candidate_id: d.university_tier for d in s.scalars(select(CandidateDemographic))}

WIDGET_PREFIXES = ("w_", "ko_", "def_", "name_", "a0_", "a1_", "a2_", "a3_", "gpa_")


def load_draft(profile: dict, meta: dict) -> None:
    for k in list(st.session_state.keys()):
        if k.startswith(WIDGET_PREFIXES):
            del st.session_state[k]
    st.session_state["draft"] = copy.deepcopy(profile)
    st.session_state["draft_meta"] = meta


if "draft" not in st.session_state and prof:
    load_draft(prof.profile_json, {"source": f"approved v{prof.version}"})

with st.expander("Job description", expanded="draft" not in st.session_state):
    jd = st.text_area("Job description (editable in Live AI mode)", value=st.session_state.get("jd", job.jd_text),
                      height=320, disabled=not live)
    st.session_state["jd"] = jd

c1, c2 = st.columns([0.35, 0.65])
if c1.button("✨ Draft success profile with AI", type="primary" if "draft" not in st.session_state else "secondary",
             disabled=confirmed):
    with st.spinner("Gemini is reading the job description…"):
        try:
            draft, res = agents.build_profile(ui.client(), jd)
            load_draft(draft, {"source": res.source, "model": res.model, "prompt_version": res.prompt_version})
            st.rerun()
        except LLMUnavailable as exc:
            st.error(str(exc))
if "draft_meta" in st.session_state:
    m = st.session_state["draft_meta"]
    c2.caption(f"Loaded: **{m.get('source')}**" + (f" · model `{m['model']}` · prompt `{m['prompt_version']}`" if m.get("model") else ""))

if "draft" not in st.session_state:
    st.info("Start by asking the AI to draft a success profile from the job description.")
    st.stop()

draft = st.session_state["draft"]
st.markdown(f"> {draft.get('role_summary', '')}")
if not live:
    st.caption("ℹ️ Demo mode: you can change **weights** and **knock-outs** (the scores update in code, no AI needed). "
               "Editing criterion wording needs Live AI mode, because the AI must re-read every CV against new wording.")

# ---------------- criteria
st.subheader("Scored criteria")
st.caption("The AI rates each criterion 0–3 against these anchors. **Code** multiplies by the weight. The AI never sees the weights.")
for c in draft["criteria"]:
    cid = c["id"]
    col1, col2 = st.columns([0.75, 0.25])
    with col1:
        c["name"] = st.text_input("Criterion", value=c["name"], key=f"name_{cid}", disabled=not live,
                                  label_visibility="collapsed")
    with col2:
        c["weight"] = int(st.number_input("Weight", 0, 100, int(c["weight"]), step=5, key=f"w_{cid}",
                                          label_visibility="collapsed"))
    with st.expander(f"Definition and anchors · {c['name']}"):
        c["definition"] = st.text_area("Definition", c["definition"], key=f"def_{cid}", disabled=not live, height=70)
        for i in range(4):
            c["anchors"][f"level_{i}"] = st.text_input(f"Level {i}", c["anchors"][f"level_{i}"], key=f"a{i}_{cid}",
                                                       disabled=not live)

total = sum(c["weight"] for c in draft["criteria"])
if total == 100:
    st.success(f"Weights sum to {total}.", icon="✅")
else:
    st.error(f"Weights sum to {total}. They must sum to 100.", icon="⚠️")

# ---------------- knock-outs
st.subheader("Knock-out rules (eligibility)")
st.caption("Checked by **rules** on the application form, not by the AI. A candidate who fails one gets a clear reason.")
for k in draft["knockouts"]:
    if k["id"] == "min_gpa":
        continue
    k["enabled"] = st.checkbox(f"{k['description']}  ·  `{k['field']} {k['operator']} {', '.join(k['values'])}`",
                               value=k.get("enabled", True), key=f"ko_{k['id']}")

gpa_rule = next((k for k in draft["knockouts"] if k["id"] == "min_gpa"), None)
use_gpa = st.checkbox("Add a minimum GPA cut-off (not in the job description)", value=bool(gpa_rule and gpa_rule.get("enabled")),
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
    by_tier = pd.Series([demo[c["candidate_id"]] for c in cut]).value_counts().to_dict() if cut else {}
    all_tier = pd.Series(list(demo.values())).value_counts().to_dict()
    st.warning(
        f"This cut-off removes **{len(cut)} of {len(cands)}** applicants before anyone reads their CV. "
        + ("By university group: " + ", ".join(f"{t}: {by_tier.get(t, 0)}/{all_tier[t]}" for t in sorted(all_tier)) + ". "
           if cut else "")
        + "GPA often reflects circumstances (for example working while studying) as much as ability. "
          "The job description does not require it, so consider leaving it off.",
        icon="⚖️",
    )
elif gpa_rule:
    gpa_rule["enabled"] = False

# ---------------- approve (HITL #1)
st.subheader("Approval")
errors = validate_profile(draft)
is_hm = ui.role() == ROLE_HM
if not is_hm:
    st.warning("Only the **Hiring Manager** can approve. Switch role in the sidebar.", icon="🔒")
for e in errors:
    st.error(e)
if prof:
    st.caption(f"Currently approved: **v{prof.version}**. Approving again creates v{prof.version + 1} and asks for screening to be re-run.")
if st.button("✅ Approve success profile", type="primary", disabled=bool(errors) or not is_hm or confirmed):
    try:
        with session_scope() as s:
            to_save = copy.deepcopy(draft)
            to_save["job_id"] = config.JOB_ID
            row = approve_profile(s, to_save, input_hash(agents.criteria_core(to_save)), ui.role(),
                                  st.session_state.get("draft_meta", {}).get("source", ""))
            v = row.version
        st.session_state["draft_meta"] = {"source": f"approved v{v}"}
        st.success(f"Success profile v{v} approved. Next: Step 2 · Screening.")
        st.page_link("views/screening.py", label="Go to Screening →")
    except WorkflowError as exc:
        st.error(str(exc))

if history:
    with st.expander("Version history"):
        st.dataframe(pd.DataFrame(history, columns=["version", "status", "approved_by", "approved_at"]),
                     hide_index=True, width="stretch")
