"""Step 3: one question at a time.

A. Approve the system's proposed shortlist (one button).
B. Decide each borderline case: invite, don't invite, or hold.
C. Confirm the shortlist.
Everything else (scorecards, evidence, the full list, overrides) sits in a collapsed section at the bottom.
"""
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
from core.workflow import (ROLE_RECRUITER, WorkflowError, bulk_approve_proposed, can_review, confirm_blockers,
                           confirm_shortlist, current_decisions, evaluations, is_confirmed, is_override,
                           record_decision)

ui.header("Shortlist review", "Decide who is invited to interview. The system has done the reading; you make "
          "the calls, one at a time.", step=3)

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

pool = {cid: e for cid, e in evs.items() if e.bucket != "Ineligible"}
by_rank = sorted(pool, key=lambda c: pool[c].rank)
proposed = [c for c in by_rank if pool[c].bucket == "Proposed"]
review = [c for c in by_rank if pool[c].bucket == "Needs Review"]
todo_prop = [c for c in proposed if c not in decisions]
todo_review = [c for c in review if c not in decisions]
n_approved = sum(d.human_decision == "Approved" for d in decisions.values())


# ---------------------------------------------------------------- helpers

def why_review(e) -> str:
    if e.flags:
        return "; ".join(FLAG_LABELS.get(f, f) for f in e.flags)
    return "The score is just below the cut-off, so a person should decide"


def strengths_gaps(e) -> tuple[list[str], list[str]]:
    strong = [a["criterion_name"] for a in sorted(e.assessments, key=lambda a: (-a["level"], -a["weight"]))
              if a["level"] >= 3][:3]
    weak = [a["criterion_name"] for a in sorted(e.assessments, key=lambda a: (a["level"], -a["weight"]))
            if a["level"] <= 1][:3]
    return strong, weak


def default_reason(e, choice: str) -> tuple[str, str]:
    if "suspicious_content" in e.flags or "unverified_quote" in e.flags:
        return ("Unverified or suspicious content",
                "Checked the flagged content; decided on the evidence that is actually in the CV.")
    if choice == "Approved":
        return ("Evidence stronger than the score suggests",
                "Read the scorecard and CV; the evidence is strong enough to interview.")
    if choice == "Rejected":
        return ("Evidence weaker than the score suggests",
                "Read the scorecard and CV; the evidence is not strong enough against the criteria.")
    return ("Other", "Keep in reserve while interview capacity is confirmed.")


def decide(cid: str, choice: str, note_text: str = "") -> None:
    e = pool[cid]
    must = e.bucket == "Needs Review" or is_override(e.bucket, choice)
    code, text = default_reason(e, choice)
    try:
        with session_scope() as s:
            record_decision(s, cid, choice, code if (must or note_text) else "",
                            (note_text.strip() or text) if (must or note_text) else "", ROLE_RECRUITER)
        st.rerun()
    except WorkflowError as exc:
        ui.note(ui.esc(exc), "bad")


def scorecard(cid: str, blind: bool) -> None:
    """Full evidence: per-criterion chart, quotes with verification, gaps, highlighted CV."""
    e, c = pool[cid], cands[cid]
    if not blind:
        st.caption(f"{c['full_name']} · {c['university']} · GPA {c['gpa']:.2f}")
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
        st.plotly_chart(ui.style_fig(fig, 40 + 40 * len(names)), width="stretch", config={"displayModeBar": False},
                        key=f"chart_{cid}_{blind}")
        st.caption("Score = sum of weight × level ÷ 3, calculated in code. Levels come from the AI, backed by quotes.")
        for a in e.assessments:
            st.markdown(f"**{a['criterion_name']}** · level {a['level']} of 3")
            st.markdown(f"<div class='muted'>{ui.esc(a['rationale'])}</div>", unsafe_allow_html=True)
            for q, okq in zip(a["evidence_quotes"], a["quotes_verified"]):
                tag = "<span class='tag ok'>In CV</span>" if okq else "<span class='tag no'>Not found in CV</span>"
                st.markdown(f"<div class='quote'>{tag}{ui.esc(q)}</div>", unsafe_allow_html=True)
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
        st.caption("CV, evidence highlighted" + (" (personal details hidden)" if blind else ""))
        st.markdown(f"<div class='cv'>{body}</div>", unsafe_allow_html=True)


def stage_bar(current: int) -> None:
    labels = ["Approve the proposed list", "Decide the borderline cases", "Confirm"]
    done = [not todo_prop, not todo_review, confirmed]
    items = []
    for i, label in enumerate(labels):
        cls = "current" if i == current else ("done" if done[i] else "")
        items.append(f"<div class='step {cls}'><span class='n'>{chr(65 + i)}</span>{label}</div>")
    st.markdown(f"<div class='stepper' style='margin-bottom:18px'>{''.join(items)}</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------- the one current question

if confirmed:
    st.markdown(f"<div class='hero'><div class='label muted'>Done</div><div class='big'>Shortlist confirmed: "
                f"{ui.plural(n_approved, 'applicant')} will be invited</div><div class='muted'>Everyone else will "
                "get a polite reply. Decisions are locked and recorded in the audit log.</div></div>",
                unsafe_allow_html=True)
    ui.next_up(3)

elif todo_prop:
    stage_bar(0)
    st.markdown(f"## The system proposes {ui.plural(len(todo_prop), 'applicant')} for interview")
    st.markdown("<div class='muted'>They have the strongest evidence against the approved criteria. "
                "Approve them all, or open any one to check first.</div>", unsafe_allow_html=True)
    rows = []
    for cid in todo_prop:
        e = pool[cid]
        strong, _ = strengths_gaps(e)
        rows.append({"Candidate": cid, "Major": cands[cid]["major"], "Score": e.total_score,
                     "Strongest on": ", ".join(strong[:2])})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={"Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100,
                                                                         format="%.0f")})
    if st.button(f"Approve all {len(todo_prop)} for interview", type="primary", key="bulk"):
        with session_scope() as s:
            bulk_approve_proposed(s, ROLE_RECRUITER)
        st.rerun()
    st.caption("Want to look at one first? Open “Browse all applicants” at the bottom of the page.")

elif todo_review:
    stage_bar(1)
    cid = todo_review[0]
    e = pool[cid]
    strong, weak = strengths_gaps(e)
    i = len(review) - len(todo_review) + 1
    st.markdown(f"<div class='label muted'>Borderline case {i} of {len(review)}</div>", unsafe_allow_html=True)
    st.markdown(f"## Invite {cid} to interview?")
    st.markdown(
        f"<div class='hero'><div class='big'>{ui.esc(cands[cid]['major'])} · score {e.total_score:.0f} of 100</div>"
        f"<div style='margin:6px 0 10px 0'><span class='tag'>Why you are asked</span>{ui.esc(why_review(e))}</div>"
        f"<div>{ui.esc(e.summary.split(chr(10))[0])}</div>"
        + (f"<div style='margin-top:10px'><b>Strong:</b> {ui.esc(', '.join(strong))}</div>" if strong else "")
        + (f"<div><b>Weak:</b> {ui.esc(', '.join(weak))}</div>" if weak else "")
        + "</div>", unsafe_allow_html=True)
    with st.expander("See the evidence and the CV"):
        scorecard(cid, blind=True)
    with st.expander("Add a note to the decision (optional)"):
        note_text = st.text_input("Note", key=f"note_{cid}", label_visibility="collapsed",
                                  placeholder="If empty, a standard reason for your answer is recorded.")
    note_text = st.session_state.get(f"note_{cid}", "")
    with st.container(horizontal=True):
        if st.button("Yes, invite", type="primary", key=f"yes_{cid}"):
            decide(cid, "Approved", note_text)
        if st.button("No, don't invite", key=f"no_{cid}"):
            decide(cid, "Rejected", note_text)
        if st.button("Not sure, hold", key=f"hold_{cid}"):
            decide(cid, "On Hold", note_text)

else:
    stage_bar(2)
    st.markdown(f"## Confirm the shortlist of {ui.plural(n_approved, 'applicant')}?")
    st.markdown("<div class='muted'>After confirming, decisions are locked and the next step sends invitations. "
                "Applicants you did not approve will get a polite reply.</div>", unsafe_allow_html=True)
    for b in blockers:
        ui.note(ui.esc(b), "plain")
    if st.button("Confirm shortlist", type="primary", disabled=bool(blockers), key="confirm"):
        try:
            with session_scope() as s:
                confirm_shortlist(s, ROLE_RECRUITER)
            st.rerun()
        except WorkflowError as exc:
            ui.note(ui.esc(exc), "bad")
    st.caption("Changed your mind about someone? Use “Browse all applicants” below.")

# ---------------------------------------------------------------- deep dive, collapsed

st.write("")
with st.expander("Browse all applicants, scorecards and decisions"):
    f1, f2 = st.columns([0.7, 0.3], vertical_alignment="bottom")
    show = f1.segmented_control("Show", ["Proposed", "Needs Review", "Not Proposed"], selection_mode="multi",
                                default=["Proposed", "Needs Review", "Not Proposed"], key="review_filter")
    blind = f2.toggle("Hide names", value=st.session_state["blind"], help="Blind review. Showing names is logged.")
    if blind != st.session_state["blind"]:
        st.session_state["blind"] = blind
        if not blind:
            with session_scope() as s:
                audit.log(s, actor_type="human", actor=ROLE_RECRUITER, action="identity_revealed",
                          reason="Recruiter turned off blind review")
    rows = []
    for cid in by_rank:
        e, c, d = pool[cid], cands[cid], decisions.get(cid)
        if e.bucket not in (show or []):
            continue
        rows.append({"Rank": e.rank, "Candidate": cid, "Name": c["full_name"], "University": c["university"],
                     "Major": c["major"], "Score": e.total_score, "System": e.bucket, "Flags": ui.flags_text(e.flags),
                     "Decision": (d.human_decision + (" (override)" if d.is_override else "")) if d else ""})
    df = pd.DataFrame(rows)
    if df.empty:
        st.caption("No applicants match this filter.")
    else:
        event = st.dataframe(
            df, hide_index=True, width="stretch", height=300, on_select="rerun", selection_mode="single-row",
            column_config={"Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100,
                                                                    format="%.1f")},
            column_order=[c for c in df.columns if not (blind and c in ("Name", "University"))], key="all_table",
        )
        sel = event.selection.rows if event and event.selection else []
        if not sel:
            st.caption("Select a row to open its scorecard.")
        else:
            cid = df.iloc[sel[0]]["Candidate"]
            e, d = pool[cid], decisions.get(cid)
            st.markdown(f"### {cid}")
            st.markdown(f"{ui.BUCKET_BADGE[e.bucket]} &nbsp; **{e.total_score:.1f}** / 100 · rank {e.rank} · "
                        f"{ui.esc(cands[cid]['major'])}"
                        + (f" · your decision: {d.human_decision}" if d else ""))
            if e.summary:
                st.markdown(f"<div class='muted'>{ui.esc(e.summary)}</div>", unsafe_allow_html=True)
            scorecard(cid, blind)
            if not confirmed:
                st.caption("Decide or change the decision for this applicant. Going against the system's suggestion "
                           "is recorded as an override with a reason.")
                with st.container(horizontal=True):
                    if st.button("Invite", key=f"d_yes_{cid}"):
                        decide(cid, "Approved")
                    if st.button("Don't invite", key=f"d_no_{cid}"):
                        decide(cid, "Rejected")
                    if st.button("Hold", key=f"d_hold_{cid}"):
                        decide(cid, "On Hold")
