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

st.title("Step 3 · Shortlist Review")
st.caption("The **Recruiter** decides. The system proposes; overrides need a written reason. "
           "Candidates flagged *Needs review* cannot be skipped.")
ui.stepper(3)

with session_scope() as s:
    ok, why = can_review(s)
    if not ok:
        st.info(why, icon="🔒")
        st.stop()
    evs = evaluations(s)
    decisions = current_decisions(s)
    confirmed = is_confirmed(s)
    blockers = confirm_blockers(s)
    cands = {c["candidate_id"]: c for c in candidate_dicts(s)}

is_rec = ui.role() == ROLE_RECRUITER
if not is_rec:
    st.warning("Only the **Recruiter** can make decisions. Switch role in the sidebar.", icon="🔒")

# ---------------- blind review toggle
blind = st.toggle("Blind review (hide name and university)", value=st.session_state["blind"],
                  help="Reduces human bias too. Revealing identity is recorded in the audit log.")
if blind != st.session_state["blind"]:
    st.session_state["blind"] = blind
    if not blind:
        with session_scope() as s:
            audit.log(s, actor_type="human", actor=ui.role(), action="identity_revealed",
                      reason="Recruiter turned off blind review")

pool = {cid: e for cid, e in evs.items() if e.bucket != "Ineligible"}
counts = {b: sum(e.bucket == b for e in pool.values()) for b in ("Proposed", "Needs Review", "Not Proposed")}
dec_counts = {d: sum(x.human_decision == d for x in decisions.values()) for d in ("Approved", "Rejected", "On Hold")}
m = st.columns(6)
m[0].metric("Proposed", counts["Proposed"])
m[1].metric("Needs review", counts["Needs Review"])
m[2].metric("Not proposed", counts["Not Proposed"])
m[3].metric("Approved", dec_counts["Approved"])
m[4].metric("Rejected / on hold", dec_counts["Rejected"] + dec_counts["On Hold"])
m[5].metric("Overrides", sum(d.is_override for d in decisions.values()))

# ---------------- candidate table
bucket_filter = st.pills("Show", ["Proposed", "Needs Review", "Not Proposed"], selection_mode="multi",
                         default=["Proposed", "Needs Review", "Not Proposed"])
rows = []
for cid, e in sorted(pool.items(), key=lambda kv: kv[1].rank):
    if e.bucket not in (bucket_filter or []):
        continue
    c = cands[cid]
    d = decisions.get(cid)
    rows.append({
        "Rank": e.rank, "Candidate": cid,
        "Name": "—" if blind else c["full_name"], "University": "—" if blind else c["university"],
        "Major": c["major"], "Score": e.total_score, "System": e.bucket,
        "Flags": ui.flags_text(e.flags),
        "Decision": (d.human_decision + (" (override)" if d.is_override else "")) if d else "",
    })
df = pd.DataFrame(rows)
if df.empty:
    st.info("No candidates match the filter.")
    st.stop()
event = st.dataframe(
    df, hide_index=True, width="stretch", height=360, on_select="rerun", selection_mode="single-row",
    column_config={"Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f")},
    column_order=[c for c in df.columns if not (blind and c in ("Name", "University"))],
)
sel_rows = event.selection.rows if event and event.selection else []
default_cid = df.iloc[sel_rows[0]]["Candidate"] if sel_rows else st.session_state.get("review_cid", df.iloc[0]["Candidate"])
if default_cid not in pool:
    default_cid = df.iloc[0]["Candidate"]
st.session_state["review_cid"] = default_cid

c1, c2 = st.columns([0.6, 0.4])
with c1:
    st.caption("Click a row to open the scorecard.")
with c2:
    if st.button(f"Approve all undecided *Proposed* ({sum(1 for cid, e in pool.items() if e.bucket == 'Proposed' and cid not in decisions)})",
                 disabled=not is_rec or confirmed, width="stretch"):
        with session_scope() as s:
            bulk_approve_proposed(s, ui.role())
        st.rerun()

# ---------------- scorecard
cid = default_cid
e = pool[cid]
c = cands[cid]
d = decisions.get(cid)
st.divider()
title = cid if blind else f"{cid} · {c['full_name']}"
st.subheader(f"Scorecard · {title}")
st.markdown(f"{ui.BUCKET_BADGE[e.bucket]} &nbsp; **Score {e.total_score:.1f} / 100** · rank {e.rank} · "
            f"{c['degree']} {c['major']} · graduating {c['graduation_year']}"
            + ("" if blind else f" · {c['university']} · GPA {c['gpa']:.2f}"))
for f in e.flags:
    st.warning(f"**{ui.FLAG_SHORT.get(f, f)}**: {FLAG_LABELS.get(f, f)}", icon="⚠️")
if e.summary:
    st.markdown(f"**AI summary:** {e.summary}")

left, right = st.columns([0.55, 0.45])
with left:
    names = [a["criterion_name"] for a in e.assessments][::-1]
    fig = go.Figure()
    labels = [f"L{a['level']} · {a['points']:.1f}/{a['weight']}" for a in e.assessments][::-1]
    fig.add_bar(y=names, x=[a["weight"] for a in e.assessments][::-1], orientation="h", marker_color=ui.TRACK,
                name="Maximum (weight)", text=labels, textposition="outside", cliponaxis=False,
                hovertemplate="%{y}<br>max %{x} points<extra></extra>")
    fig.add_bar(y=names, x=[a["points"] for a in e.assessments][::-1], orientation="h", marker_color=ui.SERIES_1,
                name="Points earned", hovertemplate="%{y}<br>%{x:.1f} points<extra></extra>")
    fig.update_layout(barmode="overlay", height=60 + 42 * len(names), margin=dict(l=0, r=40, t=10, b=0),
                      legend=dict(orientation="h", y=-0.15), xaxis=dict(range=[0, max(a["weight"] for a in e.assessments) * 1.25],
                                                                       title="points"))
    st.plotly_chart(fig, width="stretch")
    st.caption("Score = Σ weight × level ÷ 3, calculated in code. Levels come from the AI, each backed by quotes.")

    for a in e.assessments:
        with st.expander(f"{a['criterion_name']} — level {a['level']}/3 · {a['points']:.1f}/{a['weight']} pts"):
            st.markdown(f"**Why:** {a['rationale']}")
            for q, okq in zip(a["evidence_quotes"], a["quotes_verified"]):
                st.markdown(f"{'✅' if okq else '⚠️ not found in CV —'} “{q}”")
            if not a["evidence_quotes"]:
                st.caption("No evidence quoted.")
            if a.get("gaps"):
                st.markdown("**Gaps:** " + "; ".join(a["gaps"]))

with right:
    shown_cv = redact(c["cv_text"], c)[0] if blind else c["cv_text"]
    quotes = [q for a in e.assessments for q, okq in zip(a["evidence_quotes"], a["quotes_verified"]) if okq]
    body = html.escape(shown_cv)
    for q in sorted(set(quotes), key=len, reverse=True):
        pat = re.escape(html.escape(q)).replace(r"\ ", r"\s+")
        body = re.sub(pat, lambda mm: f"<mark style='background:rgba(42,120,214,.28);color:inherit;"
                      f"border-radius:3px;padding:0 2px'>{mm.group(0)}</mark>", body, flags=re.IGNORECASE)
    st.markdown("**CV** (highlighted = evidence used)" + (" · redacted view" if blind else ""))
    st.markdown(f"<div style='white-space:pre-wrap;font-size:0.85rem;max-height:620px;overflow-y:auto;"
                f"border:1px solid rgba(128,128,128,.35);border-radius:8px;padding:12px'>{body}</div>",
                unsafe_allow_html=True)

# ---------------- decision form
st.subheader("Your decision")
if d:
    st.info(f"Current decision: **{d.human_decision}**" + (" (override)" if d.is_override else "")
            + (f" · {d.reason_code}: {d.reason_text}" if d.reason_text else ""), icon="📝")
with st.form(f"decide_{cid}"):
    choice = st.radio("Decision", ["Approved", "Rejected", "On Hold"], horizontal=True,
                      format_func=lambda x: {"Approved": "Approve for interview", "Rejected": "Reject", "On Hold": "Hold"}[x])
    reason_code = st.selectbox("Reason code", [""] + REASON_CODES)
    reason_text = st.text_area("Reason (required for overrides and for every 'Needs review' decision)", height=80)
    submitted = st.form_submit_button("Save decision", type="primary", disabled=not is_rec or confirmed)
if submitted:
    try:
        with session_scope() as s:
            record_decision(s, cid, choice, reason_code, reason_text, ui.role())
        st.rerun()
    except WorkflowError as exc:
        st.error(str(exc))

# ---------------- confirm (HITL #2 gate)
st.divider()
st.subheader("Confirm shortlist")
if confirmed:
    st.success("Shortlist confirmed. Interview kits and emails are now unlocked.", icon="✅")
    st.page_link("views/outreach.py", label="Go to Interview Kit & Outreach →")
else:
    for b in blockers:
        st.warning(b, icon="⛔")
    st.caption("When you confirm, undecided *Not proposed* candidates are rejected (recorded as "
               "'Accepted system recommendation').")
    if st.button("🔒 Confirm shortlist", type="primary", disabled=bool(blockers) or not is_rec):
        try:
            with session_scope() as s:
                confirm_shortlist(s, ui.role())
            st.rerun()
        except WorkflowError as exc:
            st.error(str(exc))
