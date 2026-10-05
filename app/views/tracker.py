from collections import Counter

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import ui
from core.db import get_state, session_scope
from core.pipeline import candidate_dicts
from core.workflow import STATUS_ORDER, current_decisions, evaluations, statuses

ui.header("Tracker", "Where every applicant stands, how the funnel converts, and what the workflow saves at scale.",
          step=5)

with session_scope() as s:
    st_map = statuses(s)
    evs = evaluations(s)
    decs = current_decisions(s)
    cands = {c["candidate_id"]: c for c in candidate_dicts(s)}
    scr = get_state(s, "screening")

counts = Counter(st_map.values())
eligible = sum(1 for e in evs.values() if e.knockout_pass)
proposed = sum(1 for e in evs.values() if e.bucket in ("Proposed", "Needs Review"))
approved = sum(1 for d in decs.values() if d.human_decision == "Approved")
invited = counts["Invited"] + counts["Interview Scheduled"]

left, right = st.columns([0.6, 0.4], gap="large")
with left:
    st.markdown("## Funnel")
    stages = ["Applied", "Eligible", "Proposed or for review", "Approved", "Invited", "Interview scheduled"]
    values = [len(st_map), eligible, proposed, approved, invited, counts["Interview Scheduled"]]
    fig = go.Figure(go.Funnel(y=stages, x=values, marker_color=ui.ACCENT, textinfo="value",
                              connector={"line": {"color": ui.LINE}}, hovertemplate="%{y}: %{x}<extra></extra>"))
    st.plotly_chart(ui.style_fig(fig, 300), width="stretch", config={"displayModeBar": False})
with right:
    st.markdown("## Status")
    st.dataframe(pd.DataFrame([{"Status": k, "Applicants": counts[k]} for k in STATUS_ORDER if counts[k]]),
                 hide_index=True, width="stretch")

st.markdown("## Time at full scale")
with st.expander("Assumptions", expanded=False):
    a = st.columns(4)
    apps = a[0].number_input("Applications per year", 500, 20000, 5000, step=500)
    manual_min = a[1].number_input("Manual minutes per CV", 1.0, 30.0, 6.0, step=0.5)
    review_min = a[2].number_input("Minutes per scorecard reviewed", 1.0, 30.0, 4.0, step=0.5)
    share = a[3].number_input("Share needing review", 0.05, 1.0, round((proposed / eligible) if eligible else 0.3, 2),
                              step=0.05, help="Default: proposed or flagged applicants as a share of eligible ones.")
manual_h = apps * manual_min / 60
assisted_h = apps * share * review_min / 60
t = st.columns(3)
t[0].metric("Reading every CV", f"{manual_h:,.0f} h")
t[1].metric("With the agent", f"{assisted_h:,.0f} h", delta=f"-{manual_h - assisted_h:,.0f} h", delta_color="inverse")
ai_sec = (scr or {}).get("avg_ai_seconds_per_cv", 0) or 0
t[2].metric("AI time per CV", f"{ai_sec:.0f} s" if ai_sec else "–", help="Runs unattended and in parallel.")

st.markdown("## Applicants")
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
f1, f2, f3 = st.columns([0.4, 0.4, 0.2], vertical_alignment="bottom")
status_f = f1.multiselect("Status", [s for s in STATUS_ORDER if counts[s]], placeholder="All")
q = f2.text_input("Search", placeholder="ID, name or major")
view = df
if status_f:
    view = view[view["Status"].isin(status_f)]
if q:
    view = view[view.apply(lambda r: q.lower() in " ".join(map(str, r.values)).lower(), axis=1)]
f3.download_button("Export CSV", view.to_csv(index=False).encode("utf-8"), "candidate_tracker.csv", "text/csv",
                   width="stretch")
st.dataframe(view, hide_index=True, width="stretch", height=420,
             column_config={"Score": st.column_config.NumberColumn(format="%.1f")})
