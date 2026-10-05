import pandas as pd
import streamlit as st

import ui
from ai.client import LLMUnavailable
from core.db import session_scope
from core.models import Communication
from core.pipeline import (INTERVIEW_SLOTS, booked_slots, candidate_dicts, confirm_interview, ensure_interview_kit,
                           ensure_invite, ensure_regret, send_all_invites, send_invite, send_regrets)
from core.templates import REGRET_BODY, REGRET_SUBJECT
from core.workflow import ROLE_RECRUITER, WorkflowError, active_profile, can_generate, statuses
from sqlalchemy import select

ui.header("Interview & outreach", "Invite the shortlisted applicants and reply to everyone else. The AI drafts a "
          "tailored invitation and interview guide for each person. Emails are simulated.", step=4)

with session_scope() as s:
    ok, why = can_generate(s)
    if not ok:
        ui.note(ui.esc(why), "plain")
        st.stop()
    st_map = statuses(s)
    cands = {c["candidate_id"]: c for c in candidate_dicts(s)}
    prof = active_profile(s)
    crit_names = {c["id"]: c["name"] for c in prof.profile_json["criteria"]} if prof else {}
    slots = {r.candidate_id: r.draft_json.get("slot", "") for r in
             s.scalars(select(Communication).where(Communication.kind == "invite", Communication.sent.is_(True)))}

to_invite = sorted(c for c, v in st_map.items() if v == "Approved")
invited = sorted(c for c, v in st_map.items() if v in ("Invited", "Interview Scheduled"))
to_regret = sorted(c for c, v in st_map.items() if v in ("Rejected", "Ineligible"))
regretted = sum(v == "Regret Sent" for v in st_map.values())
on_hold = sorted(c for c, v in st_map.items() if v == "On Hold")

# ---------------- task 1: invitations
st.markdown(f"### 1. Invite {ui.plural(len(to_invite) + len(invited), 'applicant')} to interview")
if to_invite:
    st.markdown(f"<div class='muted'>Each gets a personalised invitation and a 30-minute interview slot from the "
                f"calendar. Check or edit any invitation below before sending.</div>", unsafe_allow_html=True)
    if st.button(f"Send {ui.plural(len(to_invite), 'invitation')} as Recruiter", type="primary", key="send_all"):
        with st.spinner("Preparing invitations"):
            try:
                with session_scope() as s:
                    sent, failed, no_slot = send_all_invites(s, ui.client(), ROLE_RECRUITER)
                st.session_state["invite_failed"] = failed
                st.session_state["invite_no_slot"] = no_slot
                st.rerun()
            except WorkflowError as exc:
                ui.note(ui.esc(exc), "bad")
else:
    st.markdown(f"<span class='tag ok'>Done</span> <span class='muted'>{ui.plural(len(invited), 'invitation')} sent.</span>",
                unsafe_allow_html=True)
if st.session_state.get("invite_no_slot"):
    ui.note("No free interview slot left for " + ", ".join(st.session_state["invite_no_slot"]) + ".", "warn")
if st.session_state.get("invite_failed"):
    ui.note("No saved AI draft for " + ", ".join(st.session_state["invite_failed"]) + ". Turn on Live AI in the "
            "sidebar to draft them, or send them one by one below.", "warn")

# ---------------- task 2: regrets
st.markdown(f"### 2. Reply to {ui.plural(len(to_regret) + regretted, 'applicant')} who were not shortlisted")
if to_regret:
    st.markdown("<div class='muted'>Everyone gets the same respectful message. No AI is used here on purpose, and "
                "scores are never disclosed.</div>", unsafe_allow_html=True)
    if st.button(f"Send {ui.plural(len(to_regret), 'reply', 'replies')} as Recruiter", type="primary", key="send_regrets"):
        try:
            with session_scope() as s:
                for cid in to_regret:
                    ensure_regret(s, cid)
                send_regrets(s, ROLE_RECRUITER)
            st.rerun()
        except WorkflowError as exc:
            ui.note(ui.esc(exc), "bad")
    with st.expander("Read the reply"):
        st.markdown(f"**{REGRET_SUBJECT}**")
        st.markdown(f"<div class='cv'>{ui.esc(REGRET_BODY.format(first_name='[first name]'))}</div>",
                    unsafe_allow_html=True)
else:
    st.markdown(f"<span class='tag ok'>Done</span> <span class='muted'>{ui.plural(regretted, 'reply', 'replies')} sent.</span>",
                unsafe_allow_html=True)
if on_hold:
    st.caption(f"On hold, no email yet: {', '.join(on_hold)}")

if not to_invite and not to_regret:
    ui.next_up(4)

# ---------------- deep dive: per-applicant kit and email
st.write("")
st.markdown("## Invitations and interview guides")
people = to_invite + invited
if not people:
    st.caption("No shortlisted applicants.")
    st.stop()
table = pd.DataFrame([{"Candidate": c, "Name": cands[c]["full_name"], "Major": cands[c]["major"],
                       "Status": "Not sent" if st_map[c] == "Approved" else st_map[c], "Slot": slots.get(c, "")}
                      for c in people])
st.dataframe(table, hide_index=True, width="stretch")
cid = st.selectbox("Open an applicant", people,
                   format_func=lambda x: f"{x} · {cands[x]['full_name']} · {cands[x]['major']}")
try:
    with session_scope() as s:
        kit = ensure_interview_kit(s, ui.client(), cid)
        inv = ensure_invite(s, ui.client(), cid)
        kit_json, kit_src = kit.draft_json, kit.source
        inv_json, inv_text, inv_sent = inv.draft_json, inv.edited_text, inv.sent
        taken = booked_slots(s)
except (LLMUnavailable, WorkflowError) as exc:
    ui.note(ui.esc(exc), "warn")
    st.stop()

tab_mail, tab_kit = st.tabs(["Invitation", "Interview guide"])
with tab_mail:
    st.caption("The AI never saw the name. It wrote a placeholder that the app filled in.")
    if inv_sent:
        ui.note(f"Sent (simulated) · {ui.esc(inv_json.get('slot', ''))}", "good")
        st.text_area("Body", inv_text, height=280, disabled=True)
        if st_map[cid] == "Invited" and st.button("Applicant confirmed the slot", key=f"conf_{cid}"):
            with session_scope() as s:
                confirm_interview(s, cid, ROLE_RECRUITER)
            st.rerun()
    else:
        subject = st.text_input("Subject", inv_json["subject"], key=f"subj_{cid}")
        body = st.text_area("Body", inv_text, height=280, key=f"body_{cid}")
        slot = st.selectbox("Interview slot", [x for x in INTERVIEW_SLOTS if x not in taken], key=f"slot_{cid}")
        if st.button("Send this invitation as Recruiter", key=f"send_{cid}"):
            try:
                with session_scope() as s:
                    send_invite(s, cid, subject, body, slot, ROLE_RECRUITER)
                st.rerun()
            except WorkflowError as exc:
                ui.note(ui.esc(exc), "bad")
with tab_kit:
    st.caption(f"30-minute structured interview focused on this applicant's gaps · AI draft ({kit_src})")
    if kit_json.get("focus_summary"):
        st.markdown(f"<div class='muted'>{ui.esc(kit_json['focus_summary'])}</div>", unsafe_allow_html=True)
    for i, q in enumerate(kit_json.get("questions", []), start=1):
        tag = "Probe a gap" if q["purpose"] == "probe_gap" else "Verify evidence"
        probes = " · ".join(q.get("follow_up_probes", []))
        signals = " · ".join(q.get("good_answer_signals", []))
        name = crit_names.get(q["criterion_id"], q["criterion_id"].replace("_", " "))
        st.markdown(
            f"<div class='card'><div class='label muted'>Q{i} · {tag} · {ui.esc(name)}</div>"
            f"<b>{ui.esc(q['question'])}</b>"
            + (f"<div class='muted' style='margin-top:6px'>Follow up: {ui.esc(probes)}</div>" if probes else "")
            + (f"<div class='muted'>Listen for: {ui.esc(signals)}</div>" if signals else "")
            + "</div>", unsafe_allow_html=True)
