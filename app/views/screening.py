import pandas as pd
import streamlit as st

import ui
from core import config
from core.baseline import BASELINE_GPA_MIN, baseline_select
from core.db import get_state, session_scope
from core.pipeline import candidate_dicts, run_screening
from core.redaction import redact
from core.seed import read_ground_truth
from core.workflow import WorkflowError, active_profile, can_screen, evaluations

ui.header("Screening", "Rules check eligibility, code removes personal data, the AI finds evidence for each "
          "criterion, and code verifies every quote and calculates the score.", step=2)

with session_scope() as s:
    ok, why = can_screen(s)
    scr = get_state(s, "screening")
    prof = active_profile(s)

if scr and scr.get("stale"):
    ui.note("The success profile changed after the last run. Run screening again.", "warn")

left, right = st.columns([0.3, 0.7], vertical_alignment="center")
if left.button("Run screening" if not scr else "Run again", type="primary" if not scr else "secondary",
               disabled=not ok, width="stretch"):
    bar = st.progress(0.0, text="Starting")

    def progress(done, total, cid):
        bar.progress(done / max(total, 1), text=f"{done} of {total} assessed")

    try:
        with session_scope() as s:
            run_screening(s, ui.client(), progress)
        st.rerun()
    except WorkflowError as exc:
        ui.note(ui.esc(exc), "bad")
if prof:
    right.caption(f"Profile v{prof.version} · shortlist of {config.SHORTLIST_SIZE} · borderline band "
                  f"±{config.BORDERLINE_BAND:g} points")
if not ok:
    ui.note(ui.esc(why), "plain")
if not scr:
    st.stop()

with session_scope() as s:
    evs = evaluations(s)
    cands = {c["candidate_id"]: c for c in candidate_dicts(s)}

b = scr["buckets"]
m = st.columns(4)
m[0].metric("Proposed", b.get("Proposed", 0))
m[1].metric("Needs review", b.get("Needs Review", 0))
m[2].metric("Not proposed", b.get("Not Proposed", 0))
m[3].metric("Ineligible", b.get("Ineligible", 0))

rows = []
for cid, e in sorted(evs.items(), key=lambda kv: (kv[1].rank == 0, kv[1].rank, kv[0])):
    rows.append({
        "Rank": e.rank or None, "Candidate": cid, "Major": cands[cid]["major"], "Score": e.total_score,
        "Result": e.bucket, "Flags": ui.flags_text(e.flags), "Model": e.model or "rules",
        "Reason": "; ".join(e.knockout_reasons),
    })
st.dataframe(
    pd.DataFrame(rows), hide_index=True, width="stretch", height=400,
    column_config={"Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f")},
)
models_used = sorted({e.model for e in evs.values() if e.model})
st.caption(f"Names and universities are hidden here. {len(evs)} applications · source: "
           + ", ".join(f"{k} {v}" for k, v in scr["sources"].items())
           + (f" · models: {', '.join(models_used)} (fallback chain; each scorecard shows its model)"
              if len(models_used) > 1 else ""))
st.page_link("views/review.py", label="Continue to shortlist review")

tab_ai, tab_base, tab_check = st.tabs(["What the AI sees", "Compared with a shortcut", "Planted test cases"])

with tab_ai:
    pick = st.selectbox("Applicant", sorted(cands), key="redact_pick")
    c = cands[pick]
    red, counts = redact(c["cv_text"], c)
    l, r = st.columns(2)
    l.caption("Original CV, never sent to the AI")
    l.markdown(f"<div class='cv'>{ui.esc(c['cv_text'])}</div>", unsafe_allow_html=True)
    r.caption("Sent to the AI, after automatic redaction")
    r.markdown(f"<div class='cv'>{ui.esc(red)}</div>", unsafe_allow_html=True)
    st.caption("Removed: " + (", ".join(f"{k.replace('_', ' ')} ({v})" for k, v in counts.items()) or "nothing"))

eligible = {cid for cid, e in evs.items() if e.knockout_pass}
base = baseline_select(list(cands.values()), eligible, config.SHORTLIST_SIZE)
with tab_base:
    st.caption(f"A common shortcut under volume: GPA of at least {BASELINE_GPA_MIN:.2f}, then rank by keyword count. "
               f"Same shortlist size ({config.SHORTLIST_SIZE}).")
    ours = {cid for cid, e in evs.items() if e.bucket in ("Proposed", "Needs Review")}
    theirs = {cid for cid, v in base.items() if v["baseline_selected"]}
    l, r = st.columns(2)
    with l:
        only_ours = sorted(ours - theirs, key=lambda x: evs[x].rank)
        st.markdown(f"**Found here, missed by the shortcut** · {len(only_ours)}")
        st.dataframe(pd.DataFrame([{
            "Candidate": x, "Score": evs[x].total_score, "GPA": cands[x]["gpa"],
            "Shortcut": "Cut by GPA" if not base[x]["passes_gpa"] else f"Keyword rank {base[x]['baseline_rank']}",
        } for x in only_ours]), hide_index=True, width="stretch")
    with r:
        only_theirs = sorted(theirs - ours)
        st.markdown(f"**Picked by the shortcut, not proposed here** · {len(only_theirs)}")
        st.dataframe(pd.DataFrame([{
            "Candidate": x, "Keywords": base[x]["keyword_hits"], "GPA": cands[x]["gpa"],
            "Score here": evs[x].total_score,
        } for x in only_theirs]), hide_index=True, width="stretch")

with tab_check:
    st.caption("The mock data contains planted profiles. The labels are never shown to the AI; they show whether "
               "the workflow behaves as intended.")
    gt = read_ground_truth()
    vdf = pd.DataFrame([{"Candidate": cid, "Planted profile": gt[cid]["archetype"], "Expected": gt[cid]["expected_outcome"],
                         "Result": evs[cid].bucket, "Score": evs[cid].total_score,
                         "Shortcut picks": "Yes" if base[cid]["baseline_selected"] else "No"}
                        for cid in sorted(evs) if gt[cid]["archetype"] not in ("Mid", "Weak")])

    def hit(arch, ok_buckets):
        sub = vdf[vdf["Planted profile"] == arch]
        return f"{int(sub['Result'].isin(ok_buckets).sum())} of {len(sub)}"
    k = st.columns(4)
    k[0].metric("Hidden gems surfaced", hit("Hidden gem", ["Proposed", "Needs Review"]))
    k[1].metric("Strong profiles surfaced", hit("Strong", ["Proposed", "Needs Review"]))
    k[2].metric("Keyword stuffers not proposed", hit("Keyword stuffer", ["Not Proposed", "Needs Review"]))
    inj = vdf[vdf["Planted profile"] == "Prompt injection"]["Candidate"]
    k[3].metric("Injection flagged", "Yes" if any("suspicious_content" in evs[c].flags for c in inj) else "No")
    st.dataframe(vdf, hide_index=True, width="stretch")
