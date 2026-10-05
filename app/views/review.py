import html
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import ui
from core import audit
from core.db import session_scope
from core.pipeline import candidate_dicts
from core.redaction import redact
from core.scoring import FLAG_LABELS
from core.workflow import (REASON_CODES, ROLE_RECRUITER, WorkflowError, bulk_approve_proposed, can_review,
                           confirm_blockers, confirm_shortlist, current_decisions, evaluations, is_confirmed,
                           record_decision)

ui.header("Shortlist review", "The system proposes; the Recruiter decides. Overrides and borderline cases need a "
          "written reason, and every decision is logged.", step=3)

with session_scope() as s:
    ok, why = can_review(s)
    if not ok:
        ui.note(ui.esc(why), "plain")
        st.stop()
    evs = evaluations(s)
    decisions = current_decisions(s)
    confirmed = is_confirmed(s)
    blockers = confirm_blockers(s)
    cands = {c["candidate_id"]: c for c in candidate_dicts(s)}

is_rec = ui.role() == ROLE_RECRUITER
if not is_rec:
    ui.note("Only the <b>Recruiter</b> can make decisions. Switch role in the sidebar.", "plain")

pool = {cid: e for cid, e in evs.items() if e.bucket != "Ineligible"}
counts = {b: sum(e.bucket == b for e in pool.values()) for b in ("Proposed", "Needs Review", "Not Proposed")}
undecided_prop = sum(1 for cid, e in pool.items() if e.bucket == "Proposed" and cid not in decisions)

m = st.columns(4)
m[0].metric("Proposed", counts["Proposed"])
m[1].metric("Needs review", counts["Needs Review"])
m[2].metric("Approved", sum(d.human_decision == "Approved" for d in decisions.values()))
m[3].metric("Overrides", sum(d.is_override for d in decisions.values()))

# ---------------- list
f1, f2, f3 = st.columns([0.5, 0.25, 0.25], vertical_alignment="bottom")
show = f1.segmented_control("Show", ["Proposed", "Needs Review", "Not Proposed"], selection_mode="multi",
                            default=["Proposed", "Needs Review"], key="review_filter")
blind = f2.toggle("Blind review", value=st.session_state["blind"], help="Hide name and university. Revealing is logged.")
if blind != st.session_state["blind"]:
    st.session_state["blind"] = blind
    if not blind:
        with session_scope() as s:
            audit.log(s, actor_type="human", actor=ui.role(), action="identity_revealed",
                      reason="Recruiter turned off blind review")
if f3.button(f"Approve {undecided_prop} proposed", disabled=not is_rec or confirmed or not undecided_prop,
             width="stretch"):
    with session_scope() as s:
        bulk_approve_proposed(s, ui.role())
    st.rerun()

rows = []
for cid, e in sorted(pool.items(), key=lambda kv: kv[1].rank):
    if e.bucket not in (show or []):
        continue
    c, d = cands[cid], decisions.get(cid)
    rows.append({
        "Rank": e.rank, "Candidate": cid, "Name": c["full_name"], "University": c["university"],
        "Major": c["major"], "Score": e.total_score, "System": e.bucket, "Flags": ui.flags_text(e.flags),
        "Decision": (d.human_decision + (" (override)" if d.is_override else "")) if d else "",
    })
df = pd.DataFrame(rows)
if df.empty:
    ui.note("No applicants match this filter.", "plain")
    st.stop()
event = st.dataframe(
    df, hide_index=True, width="stretch", height=320, on_select="rerun", selection_mode="single-row",
    column_config={"Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f")},
    column_order=[c for c in df.columns if not (blind and c in ("Name", "University"))],
)
sel = event.selection.rows if event and event.selection else []
cid = df.iloc[sel[0]]["Candidate"] if sel else st.session_state.get("review_cid", df.iloc[0]["Candidate"])
if cid not in set(df["Candidate"]):
    cid = df.iloc[0]["Candidate"]
st.session_state["review_cid"] = cid
st.caption("Select a row to open its scorecard.")

# ---------------- scorecard
e, c, d = pool[cid], cands[cid], decisions.get(cid)
st.divider()
st.markdown(f"## {cid}" + ("" if blind else f" · {c['full_name']}"))
st.markdown(f"{ui.BUCKET_BADGE[e.bucket]} &nbsp; **{e.total_score:.1f}** / 100 · rank {e.rank} · {c['degree']} "
            f"{c['major']} · {c['graduation_year']}" + ("" if blind else f" · {c['university']} · GPA {c['gpa']:.2f}"))
for f in e.flags:
    ui.note(f"<b>{ui.FLAG_SHORT.get(f, f)}.</b> {ui.esc(FLAG_LABELS.get(f, f))}", "warn")
if e.summary:
    st.markdown(f"<div class='muted'>{ui.esc(e.summary)}</div>", unsafe_allow_html=True)
st.caption(f"Assessed by {e.model or 'rules'} · {e.prompt_version} · profile v{e.profile_version}")

left, right = st.columns([0.55, 0.45], gap="large")
with left:
    names = [a["criterion_name"] for a in e.assessments][::-1]
    fig = go.Figure()
    fig.add_bar(y=names, x=[a["weight"] for a in e.assessments][::-1], orientation="h", marker_color=ui.TRACK,
                text=[f"{a['points']:.1f} / {a['weight']}" for a in e.assessments][::-1], textposition="outside",
                cliponaxis=False, hovertemplate="%{y}<br>max %{x} points<extra></extra>")
    fig.add_bar(y=names, x=[a["points"] for a in e.assessments][::-1], orientation="h", marker_color=ui.ACCENT,
                hovertemplate="%{y}<br>%{x:.1f} points<extra></extra>")
    fig.update_layout(barmode="overlay", bargap=0.45)
    fig.update_xaxes(range=[0, max(a["weight"] for a in e.assessments) * 1.3], title=None)
    st.plotly_chart(ui.style_fig(fig, 40 + 40 * len(names)), width="stretch", config={"displayModeBar": False})
    st.caption("Score = sum of weight × level ÷ 3, calculated in code. Levels come from the AI, backed by quotes.")

    for a in e.assessments:
        with st.expander(f"{a['criterion_name']} · level {a['level']} of 3"):
            st.markdown(ui.esc(a["rationale"]))
            for q, okq in zip(a["evidence_quotes"], a["quotes_verified"]):
                tag = "<span class='tag ok'>In CV</span>" if okq else "<span class='tag no'>Not found in CV</span>"
                st.markdown(f"<div class='quote'>{tag}{ui.esc(q)}</div>", unsafe_allow_html=True)
            if not a["evidence_quotes"]:
                st.caption("No evidence quoted.")
            if a.get("gaps"):
                st.markdown("<span class='muted'>Gaps: " + ui.esc("; ".join(a["gaps"])) + "</span>",
                            unsafe_allow_html=True)

with right:
    shown = redact(c["cv_text"], c)[0] if blind else c["cv_text"]
    body = html.escape(shown)
    quotes = {q for a in e.assessments for q, okq in zip(a["evidence_quotes"], a["quotes_verified"]) if okq}
    for q in sorted(quotes, key=len, reverse=True):
        pat = re.escape(html.escape(q)).replace(r"\ ", r"\s+")
        body = re.sub(pat, lambda mm: f"<mark>{mm.group(0)}</mark>", body, flags=re.IGNORECASE)
    st.caption("CV, evidence highlighted" + (" (redacted)" if blind else ""))
    st.markdown(f"<div class='cv'>{body}</div>", unsafe_allow_html=True)

# ---------------- decision
st.markdown("## Decision")
if d:
    st.caption(f"Current: {d.human_decision}" + (" (override)" if d.is_override else "")
               + (f" · {d.reason_code}: {d.reason_text}" if d.reason_text else ""))
with st.form(f"decide_{cid}", border=False):
    choice = st.segmented_control("Decision", ["Approved", "Rejected", "On Hold"], default="Approved",
                                  format_func=lambda x: {"Approved": "Approve", "Rejected": "Reject", "On Hold": "Hold"}[x])
    a1, a2 = st.columns([0.4, 0.6])
    reason_code = a1.selectbox("Reason code", [""] + REASON_CODES)
    reason_text = a2.text_input("Reason", placeholder="Required for overrides and for Needs review")
    submitted = st.form_submit_button("Save decision", type="primary", disabled=not is_rec or confirmed)
if submitted:
    try:
        with session_scope() as s:
            record_decision(s, cid, choice or "Approved", reason_code, reason_text, ui.role())
        st.rerun()
    except WorkflowError as exc:
        ui.note(ui.esc(exc), "bad")

# ---------------- confirm (HITL 2)
st.markdown("## Confirm shortlist")
if confirmed:
    ui.note("Shortlist confirmed. Interview kits and emails are unlocked.", "good")
    st.page_link("views/outreach.py", label="Continue to interview & outreach")
else:
    for b in blockers:
        ui.note(ui.esc(b), "plain")
    st.caption("Undecided 'Not proposed' applicants are rejected on confirmation, recorded as 'Accepted system recommendation'.")
    if st.button("Confirm shortlist", type="primary", disabled=bool(blockers) or not is_rec):
        try:
            with session_scope() as s:
                confirm_shortlist(s, ui.role())
            st.rerun()
        except WorkflowError as exc:
            ui.note(ui.esc(exc), "bad")
