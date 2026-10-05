import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import select

import ui
from ai import agents
from ai.client import LLMUnavailable
from core import config
from core.baseline import baseline_select
from core.db import session_scope
from core.fairness import FOUR_FIFTHS, impact_table
from core.models import AuditLog, CandidateDemographic
from core.pipeline import candidate_dicts
from core.redaction import redact
from core.scoring import enrich_assessments, total_score
from core.workflow import active_profile, current_decisions, evaluations

st.title("Fairness & Audit")
st.caption("Demographics are stored in a separate table. They are never sent to the AI and are used only here, "
           "to monitor outcomes.")

with session_scope() as s:
    evs = evaluations(s)
    decs = current_decisions(s)
    cands = {c["candidate_id"]: c for c in candidate_dicts(s)}
    demo = {d.candidate_id: {"gender": d.gender, "region": d.region, "university_tier": d.university_tier}
            for d in s.scalars(select(CandidateDemographic))}
    logs = [{"time (UTC)": r.ts.strftime("%Y-%m-%d %H:%M:%S"), "actor type": r.actor_type, "actor": r.actor,
             "action": r.action, "candidate": r.candidate_id, "before": json.dumps(r.before, ensure_ascii=False),
             "after": json.dumps(r.after, ensure_ascii=False), "reason": r.reason,
             "profile v": r.profile_version or "", "prompt": r.prompt_version, "input hash": r.input_hash}
            for r in s.scalars(select(AuditLog).order_by(AuditLog.id.desc()))]
    prof = active_profile(s)

tab_fair, tab_over, tab_cons, tab_audit = st.tabs(["Adverse impact", "Overrides", "Consistency", "Audit log"])

with tab_fair:
    if not evs:
        st.info("Run screening first.")
    else:
        eligible = [cid for cid, e in evs.items() if e.knockout_pass]
        base = baseline_select(list(cands.values()), set(eligible), config.SHORTLIST_SIZE)
        stages = {"AI proposed (before human review)": "ai", "Final: approved by recruiter": "final",
                  "Comparison: keyword + GPA shortcut": "baseline"}
        stage = st.radio("Stage", list(stages), horizontal=True)
        df = pd.DataFrame([{**demo[cid], "candidate_id": cid,
                            "ai": evs[cid].bucket == "Proposed",
                            "final": bool(decs.get(cid) and decs[cid].human_decision == "Approved"),
                            "baseline": base[cid]["baseline_selected"]} for cid in eligible])
        col = stages[stage]
        if col == "final" and not decs:
            st.info("No recruiter decisions yet.")
        st.caption(f"Population: {len(eligible)} eligible applicants. Rule of thumb (four-fifths rule): each group's "
                   f"selection rate should be at least {FOUR_FIFTHS:.0%} of the highest group's rate.")
        for attr, label in (("gender", "Gender"), ("university_tier", "University group"), ("region", "Home region")):
            t = impact_table(df, attr, col)
            st.markdown(f"#### {label}")
            l, r = st.columns([0.45, 0.55])
            with l:
                below = t["impact_ratio"] < FOUR_FIFTHS
                fig = go.Figure(go.Bar(
                    x=t["selection_rate"] * 100, y=t["group"], orientation="h",
                    marker_color=[ui.CRITICAL if b else ui.SERIES_1 for b in below],
                    text=[f"{v:.0%}" + (" · below 0.8" if b else "") for v, b in zip(t["selection_rate"], below)],
                    textposition="outside", hovertemplate="%{y}: %{x:.0f}%<extra></extra>"))
                top = t["selection_rate"].max() * 100
                if top > 0:
                    fig.add_vline(x=top * FOUR_FIFTHS, line_dash="dot", line_color="#8a8a86",
                                  annotation_text="0.8 × highest", annotation_position="top")
                fig.update_layout(height=70 + 38 * len(t), margin=dict(l=0, r=60, t=24, b=0),
                                  xaxis=dict(title="selection rate (%)", range=[0, max(top * 1.35, 10)]))
                st.plotly_chart(fig, width="stretch")
            with r:
                st.dataframe(t, hide_index=True, width="stretch",
                             column_config={"selection_rate": st.column_config.NumberColumn(format="%.2f")})
        st.info("With 40 mock applicants most groups are small, so these ratios are a **signal to investigate**, not "
                "statistical proof. At 5,000 applications per year the same monitor becomes meaningful. A flag means: "
                "read the scorecards for that group and check whether the criteria or the evidence explain the gap.",
                icon="ℹ️")

with tab_over:
    overrides = [d for d in decs.values() if d.is_override]
    reviewed = [d for d in decs.values() if d.system_bucket == "Needs Review"]
    m = st.columns(3)
    m[0].metric("Decisions", len(decs))
    m[1].metric("Overrides of the system", len(overrides))
    m[2].metric("'Needs review' decisions", len(reviewed))
    rows = [{"candidate": d.candidate_id, "system": d.system_bucket, "decision": d.human_decision,
             "override": d.is_override, "reason code": d.reason_code, "reason": d.reason_text, "by": d.decided_by}
            for d in decs.values() if d.is_override or d.system_bucket == "Needs Review"]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("A high override rate means the criteria or the AI need recalibrating. A zero override rate over time "
               "can mean rubber-stamping. Both are worth watching.")

with tab_cons:
    st.caption("Does the AI give the same answer twice for the same CV? Scores should differ by no more than 5 points.")
    pre = config.CACHE_DIR / "consistency.json"
    if pre.exists():
        data = json.loads(pre.read_text(encoding="utf-8"))
        st.markdown(f"**Precomputed check** (model `{data.get('model', '')}`, {data.get('created_at', '')[:10]}): "
                    "each CV assessed twice in independent calls.")
        st.dataframe(pd.DataFrame(data["rows"]), hide_index=True, width="stretch")
    if st.session_state["mode"] == "live" and evs and prof:
        pick = st.selectbox("Re-assess one candidate live", sorted(cid for cid, e in evs.items() if e.knockout_pass))
        if st.button("Run again with Gemini"):
            c = cands[pick]
            red, _ = redact(c["cv_text"], c)
            try:
                out, res = agents.extract_evidence(ui.client(), red, prof.profile_json)
                enriched, _ = enrich_assessments(out["assessments"], prof.profile_json["criteria"], red)
                new = total_score(enriched)
                old_levels = {a["criterion_id"]: a["level"] for a in evs[pick].assessments}
                diffs = {a["criterion_id"]: (old_levels.get(a["criterion_id"]), a["level"]) for a in enriched}
                st.metric("Score", f"{new:.1f}", delta=f"{new - evs[pick].total_score:+.1f} vs stored")
                st.dataframe(pd.DataFrame([{"criterion": k, "stored level": v[0], "new level": v[1]} for k, v in diffs.items()]),
                             hide_index=True)
            except LLMUnavailable as exc:
                st.error(str(exc))
    elif not pre.exists():
        st.info("Switch to Live AI mode to run a consistency check.")

with tab_audit:
    st.caption("Append-only record of every AI output, rule result and human decision, with the profile version, "
               "prompt version and an input hash so any result can be traced and reproduced.")
    adf = pd.DataFrame(logs)
    if adf.empty:
        st.info("No events yet.")
    else:
        f = st.columns(3)
        actor_f = f[0].multiselect("Actor type", sorted(adf["actor type"].unique()), placeholder="All")
        action_f = f[1].multiselect("Action", sorted(adf["action"].unique()), placeholder="All")
        cand_f = f[2].text_input("Candidate ID")
        v = adf
        if actor_f:
            v = v[v["actor type"].isin(actor_f)]
        if action_f:
            v = v[v["action"].isin(action_f)]
        if cand_f:
            v = v[v["candidate"].str.contains(cand_f.strip(), case=False)]
        st.dataframe(v, hide_index=True, width="stretch", height=480)
        st.download_button("⬇ Export audit log (CSV)", v.to_csv(index=False).encode("utf-8"), "audit_log.csv", "text/csv")
