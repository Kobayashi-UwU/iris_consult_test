import pandas as pd
import streamlit as st

import ui
from core import config
from core.baseline import BASELINE_GPA_MIN, baseline_select
from core.db import get_state, session_scope
from core.pipeline import candidate_dicts, run_screening
from core.redaction import redact
from core.seed import read_ground_truth
from core.workflow import WorkflowError, can_screen, evaluations, is_confirmed

ui.header("Screening", "Check all 40 applications against the approved profile. Personal details are removed "
          "before the AI reads anything, and every score is backed by quotes from the CV.", step=2)

with session_scope() as s:
    ok, why = can_screen(s)
    scr = get_state(s, "screening")
    confirmed = is_confirmed(s)


def run() -> None:
    bar = st.progress(0.0, text="Starting")

    def progress(done, total, cid):
        bar.progress(done / max(total, 1), text=f"Reading application {done} of {total}")

    try:
        with session_scope() as s:
            run_screening(s, ui.client(), progress)
        st.rerun()
    except WorkflowError as exc:
        ui.note(ui.esc(exc), "bad")


if not scr or scr.get("stale"):
    if scr and scr.get("stale"):
        ui.note("The success profile changed since the last run, so the results are out of date.", "warn")
    st.markdown(
        "<div class='hero'><div class='big'>Screen 40 applications</div><div class='muted'>For each applicant: "
        "eligibility rules, then personal details removed, then the AI quotes evidence for each criterion, then "
        "code checks the quotes and calculates the score. Takes a few seconds in demo mode.</div></div>",
        unsafe_allow_html=True)
    if st.button("Screen the applications", type="primary", disabled=not ok):
        run()
    if not ok:
        ui.note(ui.esc(why), "plain")
    st.stop()

with session_scope() as s:
    evs = evaluations(s)
    cands = {c["candidate_id"]: c for c in candidate_dicts(s)}

b = scr["buckets"]
st.markdown(
    f"<div class='hero'><div class='label muted'>Result</div><div class='big'>{b.get('Proposed', 0)} proposed for "
    f"interview, {b.get('Needs Review', 0)} need your judgement</div><div class='muted'>"
    f"{b.get('Not Proposed', 0)} not proposed and {b.get('Ineligible', 0)} ineligible. Nobody is contacted until "
    f"the Recruiter confirms the shortlist in the next step.</div></div>", unsafe_allow_html=True)
m = st.columns(4)
m[0].metric("Proposed", b.get("Proposed", 0))
m[1].metric("Need your judgement", b.get("Needs Review", 0))
m[2].metric("Not proposed", b.get("Not Proposed", 0))
m[3].metric("Ineligible", b.get("Ineligible", 0))
ui.next_up(2)

# ---------------- deeper detail
st.write("")
st.markdown("## Details")
tab_res, tab_ai, tab_base, tab_check = st.tabs(
    ["All results", "What the AI sees", "Compared with a shortcut", "Planted test cases"])

with tab_res:
    rows = [{
        "Rank": e.rank or None, "Candidate": cid, "Major": cands[cid]["major"], "Score": e.total_score,
        "Result": e.bucket, "Flags": ui.flags_text(e.flags), "Model": e.model or "rules",
        "Reason": "; ".join(e.knockout_reasons),
    } for cid, e in sorted(evs.items(), key=lambda kv: (kv[1].rank == 0, kv[1].rank, kv[0]))]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=420,
                 column_config={"Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100,
                                                                         format="%.1f")})
    models_used = sorted({e.model for e in evs.values() if e.model})
    st.caption("Names and universities are hidden here. Shortlist size "
               f"{config.SHORTLIST_SIZE}; applicants within {config.BORDERLINE_BAND:g} points of the cut-off, or with "
               "a flag, need a human decision."
               + (f" Models used: {', '.join(models_used)} (fallback chain; each scorecard names its model)."
                  if len(models_used) > 1 else ""))
    if st.button("Run screening again", disabled=confirmed or not ok):
        run()

with tab_ai:
    pick = st.selectbox("Applicant", sorted(cands), key="redact_pick")
    c = cands[pick]
    red, counts = redact(c["cv_text"], c)
    l, r = st.columns(2)
    l.caption("Original CV, never sent to the AI")
    l.markdown(f"<div class='cv'>{ui.esc(c['cv_text'])}</div>", unsafe_allow_html=True)
    r.caption("What the AI receives")
    r.markdown(f"<div class='cv'>{ui.esc(red)}</div>", unsafe_allow_html=True)
    st.caption("Removed: " + (", ".join(f"{k.replace('_', ' ')} ({v})" for k, v in counts.items()) or "nothing"))

eligible = {cid for cid, e in evs.items() if e.knockout_pass}
base = baseline_select(list(cands.values()), eligible, config.SHORTLIST_SIZE)
with tab_base:
    st.caption(f"A common shortcut under volume: GPA of at least {BASELINE_GPA_MIN:.2f}, then rank by keyword count, "
               f"same shortlist size ({config.SHORTLIST_SIZE}).")
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
