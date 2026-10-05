from collections import Counter

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import ui
from core.db import get_state, session_scope
from core.pipeline import candidate_dicts
from core.workflow import STATUS_ORDER, current_decisions, evaluations, statuses

st.title("Step 5 · Tracker & Funnel")
ui.stepper(5)

with session_scope() as s:
    st_map = statuses(s)
    evs = evaluations(s)
    decs = current_decisions(s)
    cands = {c["candidate_id"]: c for c in candidate_dicts(s)}
    scr = get_state(s, "screening")

# ---------------- funnel
counts = Counter(st_map.values())
n = len(st_map)
eligible = sum(1 for e in evs.values() if e.knockout_pass) if evs else 0
proposed = sum(1 for e in evs.values() if e.bucket in ("Proposed", "Needs Review"))
approved = sum(1 for d in decs.values() if d.human_decision == "Approved")
invited = counts["Invited"] + counts["Interview Scheduled"]
scheduled = counts["Interview Scheduled"]
stages = ["Applied", "Eligible", "Proposed or for review (AI + rules)", "Approved by recruiter", "Invited", "Interview scheduled"]
values = [n, eligible, proposed, approved, invited, scheduled]

left, right = st.columns([0.55, 0.45])
with left:
    st.subheader("Funnel")
    fig = go.Figure(go.Funnel(y=stages, x=values, marker_color=ui.SERIES_1, textinfo="value+percent initial",
                              hovertemplate="%{y}: %{x}<extra></extra>"))
    fig.update_layout(height=340, margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, width="stretch")
with right:
    st.subheader("Status now")
    st.dataframe(pd.DataFrame([{"Status": k, "Candidates": counts[k]} for k in STATUS_ORDER if counts[k]]),
                 hide_index=True, width="stretch")

# ---------------- time saved
st.subheader("Time saved at full scale")
st.caption("Editable assumptions. The AI time per CV comes from the actual screening run.")
a = st.columns(4)
apps = a[0].number_input("Applications per year", 500, 20000, 5000, step=500)
manual_min = a[1].number_input("Manual minutes per CV", 1.0, 30.0, 6.0, step=0.5)
review_min = a[2].number_input("Recruiter minutes per reviewed scorecard", 1.0, 30.0, 4.0, step=0.5)
review_share = (proposed / eligible) if eligible else 0.3
share = a[3].number_input("Share needing human review", 0.05, 1.0, round(review_share, 2), step=0.05,
                          help="Default = Proposed + Needs review as a share of eligible applicants in this run.")
manual_h = apps * manual_min / 60
assisted_h = apps * share * review_min / 60
ai_sec = (scr or {}).get("avg_ai_seconds_per_cv", 0) or 0
t = st.columns(3)
t[0].metric("Manual screening", f"{manual_h:,.0f} h", help="≈ recruiter working weeks at 37.5 h/week")
t[1].metric("With the agent (human review time)", f"{assisted_h:,.0f} h", delta=f"-{manual_h - assisted_h:,.0f} h",
            delta_color="inverse")
t[2].metric("AI processing per CV", f"{ai_sec:.1f} s" if ai_sec else "—",
            help="Runs in parallel and unattended; not recruiter time.")
st.caption(f"Manual ≈ {manual_h / 37.5:.0f} recruiter-weeks vs ≈ {assisted_h / 37.5:.1f} with the agent. "
           "Every applicant still gets a full read of their CV, not just the top of the pile.")

# ---------------- tracker
st.subheader("Candidate tracker")
rows = []
for cid in sorted(cands):
    c, e, d = cands[cid], evs.get(cid), decs.get(cid)
    rows.append({
        "Candidate": cid, "Name": c["full_name"], "Major": c["major"], "Applied": c["application_date"],
        "Source": c["source_channel"], "Score": e.total_score if e else None, "System": e.bucket if e else "",
        "Decision": d.human_decision if d else "", "Override": bool(d and d.is_override),
        "Reason": (d.reason_text or d.reason_code) if d else "", "Status": st_map.get(cid, "Applied"),
    })
df = pd.DataFrame(rows)
f1, f2 = st.columns([0.4, 0.6])
status_f = f1.multiselect("Status", [s for s in STATUS_ORDER if counts[s]], placeholder="All")
q = f2.text_input("Search", placeholder="Candidate ID, name or major")
view = df
if status_f:
    view = view[view["Status"].isin(status_f)]
if q:
    ql = q.lower()
    view = view[view.apply(lambda r: ql in " ".join(map(str, r.values)).lower(), axis=1)]
st.dataframe(view, hide_index=True, width="stretch", height=420,
             column_config={"Score": st.column_config.NumberColumn(format="%.1f")})
st.download_button("⬇ Export tracker (CSV)", view.to_csv(index=False).encode("utf-8"), "candidate_tracker.csv", "text/csv")
