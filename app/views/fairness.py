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

ui.header("Fairness & audit", "Demographics are stored separately and never sent to the AI. They are used only "
          "here, to monitor outcomes. Every AI output and human decision is logged.")

with session_scope() as s:
    evs = evaluations(s)
    decs = current_decisions(s)
    cands = {c["candidate_id"]: c for c in candidate_dicts(s)}
    demo = {d.candidate_id: {"gender": d.gender, "region": d.region, "university_tier": d.university_tier}
            for d in s.scalars(select(CandidateDemographic))}
    logs = [{"Time (UTC)": r.ts.strftime("%Y-%m-%d %H:%M:%S"), "Actor": f"{r.actor_type}: {r.actor}",
             "Action": r.action, "Candidate": r.candidate_id, "Before": json.dumps(r.before, ensure_ascii=False),
             "After": json.dumps(r.after, ensure_ascii=False), "Reason": r.reason,
             "Profile": r.profile_version or "", "Prompt": r.prompt_version, "Input hash": r.input_hash}
            for r in s.scalars(select(AuditLog).order_by(AuditLog.id.desc()))]
    prof = active_profile(s)

tab_fair, tab_over, tab_cons, tab_audit = st.tabs(["Adverse impact", "Overrides", "Consistency", "Audit log"])

with tab_fair:
    if not evs:
        ui.note("Run screening first.", "plain")
    else:
        eligible = [cid for cid, e in evs.items() if e.knockout_pass]
        base = baseline_select(list(cands.values()), set(eligible), config.SHORTLIST_SIZE)
        stages = {"AI proposed": "ai", "Recruiter approved": "final", "Keyword + GPA shortcut": "baseline"}
        stage = st.segmented_control("Stage", list(stages), default="AI proposed", key="fair_stage") or "AI proposed"
        col = stages[stage]
        df = pd.DataFrame([{**demo[cid], "ai": evs[cid].bucket == "Proposed",
                            "final": bool(decs.get(cid) and decs[cid].human_decision == "Approved"),
                            "baseline": base[cid]["baseline_selected"]} for cid in eligible])
        if col == "final" and not decs:
            ui.note("No recruiter decisions yet.", "plain")
        st.caption(f"{len(eligible)} eligible applicants. Four-fifths rule: each group's selection rate should be at "
                   f"least {FOUR_FIFTHS:.0%} of the highest group's rate.")
        for attr, label in (("gender", "Gender"), ("university_tier", "University group"), ("region", "Home region")):
            t = impact_table(df, attr, col)
            st.markdown(f"### {label}")
            l, r = st.columns([0.45, 0.55], gap="large")
            with l:
                below = t["impact_ratio"] < FOUR_FIFTHS
                top = t["selection_rate"].max() * 100
                fig = go.Figure(go.Bar(
                    x=t["selection_rate"] * 100, y=t["group"], orientation="h",
                    marker_color=[ui.BAD if b else ui.ACCENT for b in below],
                    text=[f"{v:.0%}" + (" · below 0.8" if b else "") for v, b in zip(t["selection_rate"], below)],
                    textposition="outside", cliponaxis=False, hovertemplate="%{y}: %{x:.0f}%<extra></extra>"))
                if top > 0:
                    fig.add_vline(x=top * FOUR_FIFTHS, line_dash="dot", line_color=ui.MUTED)
                fig.update_layout(bargap=0.45)
                fig.update_xaxes(range=[0, max(top * 1.4, 10)], title=None)
                st.plotly_chart(ui.style_fig(fig, 30 + 34 * len(t)), width="stretch",
                                config={"displayModeBar": False})
            with r:
                st.dataframe(t.rename(columns={"group": "Group", "n": "n", "selected": "Selected",
                                               "selection_rate": "Rate", "impact_ratio": "Ratio", "status": "Status"})
                             .drop(columns=["small_sample"]), hide_index=True, width="stretch",
                             column_config={"Rate": st.column_config.NumberColumn(format="%.2f")})
        ui.note("With 40 applicants most groups are small, so a ratio below 0.8 is a signal to read those scorecards, "
                "not statistical proof. At 5,000 applications a year the same monitor becomes meaningful.", "plain")

with tab_over:
    overrides = [d for d in decs.values() if d.is_override]
    m = st.columns(3)
    m[0].metric("Decisions", len(decs))
    m[1].metric("Overrides", len(overrides))
    m[2].metric("Needs-review decisions", sum(d.system_bucket == "Needs Review" for d in decs.values()))
    rows = [{"Candidate": d.candidate_id, "System": d.system_bucket, "Decision": d.human_decision,
             "Override": d.is_override, "Reason code": d.reason_code, "Reason": d.reason_text, "By": d.decided_by}
            for d in decs.values() if d.is_override or d.system_bucket == "Needs Review"]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("A high override rate means the criteria or the AI need recalibrating. A rate of zero over time can "
               "mean rubber-stamping.")

with tab_cons:
    st.caption("Does the AI give the same answer twice for the same CV? Target: no more than 5 points apart.")
    pre = config.CACHE_DIR / "consistency.json"
    if pre.exists():
        data = json.loads(pre.read_text(encoding="utf-8"))
        st.markdown(f"Precomputed check, {data.get('created_at', '')[:10]}: each CV assessed twice in independent "
                    "calls on the same model.")
        st.dataframe(pd.DataFrame(data["rows"]), hide_index=True, width="stretch")
    if st.session_state["mode"] == "live" and evs and prof:
        pick = st.selectbox("Re-assess one applicant live", sorted(cid for cid, e in evs.items() if e.knockout_pass))
        if st.button("Run again"):
            c = cands[pick]
            red, _ = redact(c["cv_text"], c)
            try:
                out, res = agents.extract_evidence(ui.client(), red, prof.profile_json)
                enriched, _ = enrich_assessments(out["assessments"], prof.profile_json["criteria"], red)
                new = total_score(enriched)
                old = {a["criterion_id"]: a["level"] for a in evs[pick].assessments}
                st.metric("Score", f"{new:.1f}", delta=f"{new - evs[pick].total_score:+.1f} vs stored")
                st.dataframe(pd.DataFrame([{"Criterion": a["criterion_name"], "Stored": old.get(a["criterion_id"]),
                                            "New": a["level"]} for a in enriched]), hide_index=True)
                st.caption(f"Model: {res.model}")
            except LLMUnavailable as exc:
                ui.note(ui.esc(exc), "warn")
    elif not pre.exists():
        ui.note("Turn on Live AI to run a consistency check.", "plain")

with tab_audit:
    st.caption("Append-only. Each entry carries the profile version, prompt version and an input hash, so any result "
               "can be traced and reproduced.")
    adf = pd.DataFrame(logs)
    if adf.empty:
        ui.note("No events yet.", "plain")
    else:
        f = st.columns([0.3, 0.3, 0.2, 0.2], vertical_alignment="bottom")
        actor_f = f[0].multiselect("Actor", sorted(adf["Actor"].str.split(":").str[0].unique()), placeholder="All")
        action_f = f[1].multiselect("Action", sorted(adf["Action"].unique()), placeholder="All")
        cand_f = f[2].text_input("Candidate")
        v = adf
        if actor_f:
            v = v[v["Actor"].str.split(":").str[0].isin(actor_f)]
        if action_f:
            v = v[v["Action"].isin(action_f)]
        if cand_f:
            v = v[v["Candidate"].str.contains(cand_f.strip(), case=False)]
        f[3].download_button("Export CSV", v.to_csv(index=False).encode("utf-8"), "audit_log.csv", "text/csv",
                             width="stretch")
        st.dataframe(v, hide_index=True, width="stretch", height=480)
