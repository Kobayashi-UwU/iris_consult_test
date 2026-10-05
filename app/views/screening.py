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

st.title("Step 2 · Screening")
st.caption("Rules check eligibility → code removes personal data → the AI finds evidence for each criterion → "
           "code verifies every quote and calculates the score.")
ui.stepper(2)

with session_scope() as s:
    ok, why = can_screen(s)
    scr = get_state(s, "screening")
    prof = active_profile(s)

if prof:
    st.caption(f"Using success profile **v{prof.version}** · shortlist size {config.SHORTLIST_SIZE} · "
               f"borderline band ±{config.BORDERLINE_BAND:g} points")
if scr and scr.get("stale"):
    st.warning("The success profile changed after the last run. Run screening again.", icon="🔁")

label = "▶ Run screening on 40 applications" if not scr else "🔁 Re-run screening"
if st.button(label, type="primary", disabled=not ok):
    bar = st.progress(0.0, text="Starting…")

    def progress(done, total, cid):
        bar.progress(done / max(total, 1), text=f"Assessed {done}/{total} · {cid}")

    try:
        with session_scope() as s:
            run_screening(s, ui.client(), progress)
        st.rerun()
    except WorkflowError as exc:
        st.error(str(exc))
if not ok:
    st.info(why, icon="🔒")

if not scr:
    st.stop()

# ---------------- results
with session_scope() as s:
    evs = evaluations(s)
    cands = {c["candidate_id"]: c for c in candidate_dicts(s)}

b = scr["buckets"]
m = st.columns(5)
m[0].metric("Applications", len(evs))
m[1].metric("Ineligible", b.get("Ineligible", 0))
m[2].metric("Proposed", b.get("Proposed", 0))
m[3].metric("Needs review", b.get("Needs Review", 0))
m[4].metric("Not proposed", b.get("Not Proposed", 0))
src = ", ".join(f"{k}: {v}" for k, v in scr["sources"].items())
st.caption(f"Run at {scr['run_at'][:19].replace('T', ' ')} UTC · mode **{scr['mode']}** · result sources: {src} · "
           f"average AI time per CV {scr['avg_ai_seconds_per_cv']}s · run time {scr['wall_seconds']}s")
models_used = sorted({e.model for e in evs.values() if e.model})
if len(models_used) > 1:
    st.caption(f"ℹ️ Models used: {', '.join(models_used)}. The app uses a fallback chain (most capable first) because "
               "free-tier quotas are per model. Each scorecard shows its model; in production one paid model would "
               "assess every CV for consistency.")

rows = []
for cid, e in sorted(evs.items(), key=lambda kv: (kv[1].rank == 0, kv[1].rank, kv[0])):
    c = cands[cid]
    rows.append({
        "Rank": e.rank or None, "Candidate": cid, "Major": c["major"], "Score": e.total_score,
        "Bucket": e.bucket, "Flags": ui.flags_text(e.flags), "AI model": e.model or "rules",
        "Why ineligible": "; ".join(e.knockout_reasons),
    })
df = pd.DataFrame(rows)
st.dataframe(
    df, hide_index=True, width="stretch", height=420,
    column_config={"Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f")},
)
st.caption("Names and universities are hidden here on purpose. Open **Shortlist Review** for full scorecards.")
st.page_link("views/review.py", label="Go to Shortlist Review →")

# ---------------- what the AI sees
st.subheader("What the AI sees")
pick = st.selectbox("Candidate", sorted(cands), key="redact_pick")
c = cands[pick]
red, counts = redact(c["cv_text"], c)
l, r = st.columns(2)
l.markdown("**Original CV** (never sent to the AI)")
l.code(c["cv_text"], language=None, wrap_lines=True)
r.markdown("**Sent to the AI** (after automatic redaction)")
r.code(red, language=None, wrap_lines=True)
st.caption("Redacted: " + (", ".join(f"{k.replace('_', ' ')} ×{v}" for k, v in counts.items()) or "nothing found"))

# ---------------- baseline comparison
st.subheader("Compared with a typical manual shortcut")
st.caption(f"Assumption: under volume pressure, screens often shortcut to *GPA ≥ {BASELINE_GPA_MIN:.2f}, then count "
           f"keywords*. Same shortlist size ({config.SHORTLIST_SIZE}).")
eligible = {cid for cid, e in evs.items() if e.knockout_pass}
base = baseline_select(list(cands.values()), eligible, config.SHORTLIST_SIZE)
ours = {cid for cid, e in evs.items() if e.bucket in ("Proposed", "Needs Review")}
theirs = {cid for cid, v in base.items() if v["baseline_selected"]}
only_ours = sorted(ours - theirs, key=lambda x: evs[x].rank)
only_theirs = sorted(theirs - ours)
cc = st.columns(2)
with cc[0]:
    st.markdown(f"**Found by evidence-based screening, missed by the shortcut ({len(only_ours)})**")
    st.dataframe(pd.DataFrame([{
        "Candidate": x, "Score": evs[x].total_score, "GPA": cands[x]["gpa"],
        "Shortcut result": "Cut by GPA" if not base[x]["passes_gpa"] else f"Keyword rank {base[x]['baseline_rank']}",
    } for x in only_ours]), hide_index=True, width="stretch")
with cc[1]:
    st.markdown(f"**Picked by the shortcut, not proposed here ({len(only_theirs)})**")
    st.dataframe(pd.DataFrame([{
        "Candidate": x, "Keyword hits": base[x]["keyword_hits"], "GPA": cands[x]["gpa"],
        "Our score": evs[x].total_score, "Our bucket": evs[x].bucket,
    } for x in only_theirs]), hide_index=True, width="stretch")

# ---------------- validation against planted ground truth
with st.expander("Prototype check against the planted test cases (mock data only)"):
    st.caption("The mock dataset contains planted profiles. These labels are never shown to the AI; they let us "
               "check that the workflow behaves as intended.")
    gt = read_ground_truth()
    vrows = [{"Candidate": cid, "Planted profile": gt[cid]["archetype"], "Expected": gt[cid]["expected_outcome"],
              "Result": evs[cid].bucket, "Score": evs[cid].total_score,
              "Shortcut would pick": "Yes" if base[cid]["baseline_selected"] else "No"}
             for cid in sorted(evs) if gt[cid]["archetype"] not in ("Mid", "Weak")]
    vdf = pd.DataFrame(vrows)
    st.dataframe(vdf, hide_index=True, width="stretch")

    def hit(arch, ok_buckets):
        sub = vdf[vdf["Planted profile"] == arch]
        return f"{int(sub['Result'].isin(ok_buckets).sum())}/{len(sub)}"
    k = st.columns(4)
    k[0].metric("Hidden gems surfaced", hit("Hidden gem", ["Proposed", "Needs Review"]))
    k[1].metric("Strong profiles surfaced", hit("Strong", ["Proposed", "Needs Review"]))
    k[2].metric("Keyword stuffers not proposed", hit("Keyword stuffer", ["Not Proposed", "Needs Review"]))
    inj = vdf[vdf["Planted profile"] == "Prompt injection"]
    k[3].metric("Injection attempt flagged", "Yes" if any("suspicious_content" in evs[c].flags for c in inj["Candidate"]) else "No")
