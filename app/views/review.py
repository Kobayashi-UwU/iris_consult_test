"""Step 3: one list, one applicant open at a time.

The list shows everyone who needs a decision. The first undecided applicant is open below it. Clicking a row opens
that applicant and scrolls to them; reading down ends at three choices. Choosing closes the applicant, scrolls back
to the list and opens the next one. When everyone has a decision, the Recruiter confirms the shortlist.
"""
import html
import re

import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

import ui
from core import audit
from core.db import session_scope
from core.pipeline import candidate_dicts
from core.redaction import redact
from core.scoring import FLAG_LABELS
from core.workflow import (ROLE_RECRUITER, WorkflowError, can_review, confirm_blockers, confirm_shortlist,
                           current_decisions, evaluations, is_confirmed, is_override, record_decision)

ui.header("Shortlist review", "Decide who is invited to interview. Open an applicant, read the evidence, and choose. "
          "The next applicant opens automatically.", step=3)

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
needed = [c for c in by_rank if pool[c].bucket in ("Proposed", "Needs Review")]
n_done = sum(1 for c in needed if c in decisions)
n_approved = sum(d.human_decision == "Approved" for d in decisions.values())
blind = st.session_state["blind"]

SUGGESTION = {"Proposed": "Invite", "Needs Review": "Your call", "Not Proposed": "Don't invite"}
DECISION_TEXT = {"Approved": "Invite", "Rejected": "Don't invite", "On Hold": "Hold"}

st.markdown(f"""<style>
[class*="st-key-row_"] {{ position: relative; }}
[class*="st-key-row_"] [data-testid="stElementContainer"], [class*="st-key-row_"] [data-testid="stMarkdownContainer"],
[class*="st-key-row_"] [data-testid="stMarkdown"] {{ margin: 0 !important; }}
[class*="st-key-rowbtn_"] {{ position: absolute !important; inset: 0 !important; z-index: 2; margin: 0 !important;
                            width: 100% !important; height: 100% !important; }}
[class*="st-key-rowbtn_"] .stButton, [class*="st-key-rowbtn_"] button {{ width: 100% !important; height: 100% !important;
                            min-height: 0 !important; padding: 0 !important; }}
[class*="st-key-rowbtn_"] button {{ opacity: 0; cursor: pointer; }}
[class*="st-key-row_"]:hover .rowx {{ background: #f3f6f6; }}
.rowx {{ display: grid; grid-template-columns: 44px 84px 1fr 120px 110px 120px; gap: 8px; align-items: center;
        padding: 10px 14px; border-bottom: 1px solid {ui.LINE}; background: #fff; font-size: 0.9rem; }}
.rowx.head {{ background: #f4f4f0; color: {ui.MUTED}; font-size: 0.75rem; text-transform: uppercase; letter-spacing: .05em;
             border-radius: 10px 10px 0 0; border: 1px solid {ui.LINE}; }}
.rowx.sel {{ background: {ui.ACCENT_SOFT}; box-shadow: inset 3px 0 0 {ui.ACCENT}; font-weight: 600; }}
.rowlist {{ border-left: 1px solid {ui.LINE}; border-right: 1px solid {ui.LINE}; }}
.bar {{ height: 6px; background: {ui.TRACK}; border-radius: 3px; overflow: hidden; }}
.bar > span {{ display: block; height: 100%; background: {ui.ACCENT}; }}
</style>""", unsafe_allow_html=True)


# ---------------------------------------------------------------- helpers

def why_review(e) -> str:
    if e.flags:
        return "; ".join(FLAG_LABELS.get(f, f) for f in e.flags)
    return "The score is just below the cut-off, so a person should decide"


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


def visible_ids() -> list[str]:
    show = st.session_state.get("show_all_rows", False)
    return by_rank if show else needed


def next_open(after: str | None) -> str | None:
    """First undecided applicant after `after` in list order, wrapping around; None when all are decided."""
    order = visible_ids()
    undecided = [c for c in order if c not in decisions or c == after]
    if after in order:
        i = order.index(after)
        rest = [c for c in order[i + 1:] + order[:i] if c not in decisions and c != after]
        return rest[0] if rest else None
    return undecided[0] if undecided else None


def decide(cid: str, choice: str) -> None:
    e = pool[cid]
    note_text = st.session_state.get(f"note_{cid}", "").strip()
    must = e.bucket == "Needs Review" or is_override(e.bucket, choice)
    code, text = default_reason(e, choice)
    try:
        with session_scope() as s:
            record_decision(s, cid, choice, code if (must or note_text) else "",
                            (note_text or text) if (must or note_text) else "", ROLE_RECRUITER)
        decisions[cid] = True  # local view, only used to pick the next applicant
        st.session_state["sel_cid"] = next_open(cid)
        st.session_state["scroll_to"] = "review-list"
        st.rerun()
    except WorkflowError as exc:
        ui.note(ui.esc(exc), "bad")


def scroll(target: str) -> None:
    """Scroll the page to an anchor. Retries because Streamlit is still laying out the page when the script runs."""
    st.session_state["_scroll_n"] = st.session_state.get("_scroll_n", 0) + 1
    components.html(
        f"<script>/*{st.session_state['_scroll_n']}*/"
        "const go=()=>{const el=window.parent.document.getElementById('" + target + "');"
        "if(el){el.scrollIntoView({block:'start'});}};"
        "[150,500,1000].forEach(ms=>setTimeout(go,ms));</script>", height=0)


# ---------------------------------------------------------------- progress + confirm

if confirmed:
    st.markdown(f"<div class='hero'><div class='label muted'>Done</div><div class='big'>Shortlist confirmed: "
                f"{ui.plural(n_approved, 'applicant')} will be invited</div><div class='muted'>Everyone else will "
                "get a polite reply. Decisions are locked and recorded in the audit log.</div></div>",
                unsafe_allow_html=True)
    ui.next_up(3)
else:
    left, right = st.columns([0.7, 0.3], vertical_alignment="center")
    with left:
        st.progress(n_done / max(len(needed), 1),
                    text=f"{n_done} of {len(needed)} decided · {ui.plural(n_approved, 'applicant')} to invite")
    with right:
        if st.button("Confirm shortlist", type="primary" if not blockers else "secondary", disabled=bool(blockers),
                     width="stretch", key="confirm"):
            try:
                with session_scope() as s:
                    confirm_shortlist(s, ROLE_RECRUITER)
                st.rerun()
            except WorkflowError as exc:
                ui.note(ui.esc(exc), "bad")
    if not blockers:
        ui.note("Everyone has a decision. Confirm the shortlist to lock it and move on to invitations. Applicants "
                "the system did not propose, and you did not open, will get a polite reply.", "good")

# ---------------------------------------------------------------- the list

st.markdown("<div id='review-list'></div>", unsafe_allow_html=True)
c1, c2 = st.columns([0.7, 0.3], vertical_alignment="center")
c1.toggle("Also show applicants the system did not propose", key="show_all_rows")
new_blind = c2.toggle("Hide names", value=blind, help="Blind review. Showing names is logged.")
if new_blind != blind:
    st.session_state["blind"] = blind = new_blind
    if not blind:
        with session_scope() as s:
            audit.log(s, actor_type="human", actor=ROLE_RECRUITER, action="identity_revealed",
                      reason="Recruiter turned off blind review")

order = visible_ids()
sel = st.session_state.get("sel_cid")
if sel not in pool:
    sel = next_open(None) or (order[0] if order else None)
    st.session_state["sel_cid"] = sel

name_col = "Name" if not blind else "Major"
st.markdown(f"<div class='rowx head'><span>Rank</span><span>ID</span><span>{name_col}</span><span>Score</span>"
            "<span>System</span><span>Your decision</span></div>", unsafe_allow_html=True)
with st.container(gap=None):
    for cid in order:
        e, c, d = pool[cid], cands[cid], decisions.get(cid)
        label = c["major"] if blind else f"{c['full_name']} · {c['major']}"
        dec = DECISION_TEXT.get(d.human_decision, "") if d is not None and d is not True else ""
        if not dec and pool[cid].bucket == "Not Proposed" and not confirmed:
            dec = "<span class='muted'>–</span>"
        with st.container(key=f"row_{cid}", gap=None):
            st.markdown(
                f"<div class='rowx {'sel' if cid == sel else ''}'><span class='muted'>{e.rank}</span><span>{cid}</span>"
                f"<span>{ui.esc(label)}</span><span style='display:flex;align-items:center;gap:8px'><div class='bar' style='flex:1'><span style='width:{e.total_score:.0f}%'></span>"
                f"</div><span class='muted' style='font-size:.8rem;min-width:22px;text-align:right'>{e.total_score:.0f}</span></span>"
                f"<span>{SUGGESTION[e.bucket]}</span><span>{dec}</span></div>", unsafe_allow_html=True)
            if st.button(f"Open {cid}", key=f"rowbtn_{cid}"):
                st.session_state["sel_cid"] = cid
                st.session_state["scroll_to"] = "applicant"
                st.rerun()

# ---------------------------------------------------------------- the open applicant

if sel:
    e, c, d = pool[sel], cands[sel], decisions.get(sel)
    st.markdown("<div id='applicant' style='height:12px'></div>", unsafe_allow_html=True)
    st.divider()
    ranked = sorted(e.assessments, key=lambda a: (-a["level"], -a["weight"]))
    strong = [a["criterion_name"] for a in ranked if a["level"] >= 3][:3]
    weak = [a["criterion_name"] for a in sorted(e.assessments, key=lambda a: (a["level"], -a["weight"]))
            if a["level"] <= 1][:3]
    st.markdown(f"<div class='label muted'>Rank {e.rank} · system suggests: {SUGGESTION[e.bucket]}</div>",
                unsafe_allow_html=True)
    st.markdown(f"## {sel} · {ui.esc(c['major'])}" + ("" if blind else f" · {ui.esc(c['full_name'])}"))
    st.markdown(
        f"<div class='hero'><div class='big'>Score {e.total_score:.0f} of 100</div>"
        + (f"<div style='margin:6px 0 10px 0'><span class='tag'>Why you are asked</span>{ui.esc(why_review(e))}</div>"
           if e.bucket == "Needs Review" else "")
        + f"<div>{ui.esc(e.summary.split(chr(10))[0])}</div>"
        + (f"<div style='margin-top:10px'><b>Strong:</b> {ui.esc(', '.join(strong))}</div>" if strong else "")
        + (f"<div><b>Weak:</b> {ui.esc(', '.join(weak))}</div>" if weak else "")
        + "</div>", unsafe_allow_html=True)
    if not blind:
        st.caption(f"{c['university']} · GPA {c['gpa']:.2f}")

    left, right = st.columns([0.55, 0.45], gap="large")
    with left:
        st.markdown("### Evidence by criterion")
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
                        key=f"chart_{sel}")
        for a in e.assessments:
            st.markdown(f"**{a['criterion_name']}** · level {a['level']} of 3")
            st.markdown(f"<div class='muted'>{ui.esc(a['rationale'])}</div>", unsafe_allow_html=True)
            for q, okq in zip(a["evidence_quotes"], a["quotes_verified"]):
                tag = "<span class='tag ok'>In CV</span>" if okq else "<span class='tag no'>Not found in CV</span>"
                st.markdown(f"<div class='quote'>{tag}{ui.esc(q)}</div>", unsafe_allow_html=True)
            if a.get("gaps"):
                st.markdown("<span class='muted'>Gaps: " + ui.esc("; ".join(a["gaps"])) + "</span>",
                            unsafe_allow_html=True)
        st.caption(f"Score = sum of weight × level ÷ 3, calculated in code. Assessed by {e.model or 'rules'} "
                   f"({e.prompt_version}, profile v{e.profile_version}).")
    with right:
        st.markdown("### CV")
        shown = redact(c["cv_text"], c)[0] if blind else c["cv_text"]
        body = html.escape(shown)
        quotes = {q for a in e.assessments for q, okq in zip(a["evidence_quotes"], a["quotes_verified"]) if okq}
        for q in sorted(quotes, key=len, reverse=True):
            pat = re.escape(html.escape(q)).replace(r"\ ", r"\s+")
            body = re.sub(pat, lambda mm: f"<mark>{mm.group(0)}</mark>", body, flags=re.IGNORECASE)
        st.caption("Evidence highlighted" + (" · personal details hidden" if blind else ""))
        st.markdown(f"<div class='cv'>{body}</div>", unsafe_allow_html=True)

    st.divider()
    if confirmed:
        st.caption(f"Decision: {DECISION_TEXT.get(d.human_decision, '-') if d else '-'} (shortlist confirmed)")
    else:
        current = DECISION_TEXT.get(d.human_decision) if d else None
        st.markdown(f"### Invite {sel} to interview?" + (f" <span class='tag'>Now: {current}</span>" if current else ""),
                    unsafe_allow_html=True)
        st.text_input("Note (optional)", key=f"note_{sel}",
                      placeholder="If empty, a standard reason for your choice is recorded in the audit log.")
        with st.container(horizontal=True):
            if st.button("Invite to interview", type="primary", key=f"yes_{sel}"):
                decide(sel, "Approved")
            if st.button("Don't invite", key=f"no_{sel}"):
                decide(sel, "Rejected")
            if st.button("Hold", key=f"hold_{sel}"):
                decide(sel, "On Hold")

target = st.session_state.pop("scroll_to", None)
if target:
    scroll(target)
