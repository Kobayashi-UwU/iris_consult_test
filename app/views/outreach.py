import streamlit as st

import ui
from ai.client import LLMUnavailable
from core.db import session_scope
from core.pipeline import (INTERVIEW_SLOTS, booked_slots, candidate_dicts, confirm_interview, ensure_interview_kit,
                           ensure_invite, ensure_regret, send_invite, send_regrets)
from core.templates import REGRET_BODY, REGRET_SUBJECT
from core.workflow import ROLE_RECRUITER, WorkflowError, active_profile, can_generate, statuses

ui.header("Interview & outreach", "The AI drafts a tailored interview kit and invitation for each approved "
          "applicant. The Recruiter edits and sends. Emails are simulated.", step=4)

with session_scope() as s:
    ok, why = can_generate(s)
    if not ok:
        ui.note(ui.esc(why), "plain")
        st.stop()
    st_map = statuses(s)
    cands = {c["candidate_id"]: c for c in candidate_dicts(s)}
    prof = active_profile(s)
    crit_names = {c["id"]: c["name"] for c in prof.profile_json["criteria"]} if prof else {}

is_rec = ui.role() == ROLE_RECRUITER
if not is_rec:
    ui.note("Only the <b>Recruiter</b> can send emails. Switch role in the sidebar.", "plain")

tab_inv, tab_reg = st.tabs(["Invitations", "Regret emails"])

with tab_inv:
    approved = sorted(cid for cid, v in st_map.items() if v in ("Approved", "Invited", "Interview Scheduled"))
    if not approved:
        ui.note("No approved applicants.", "plain")
    else:
        cid = st.selectbox("Applicant", approved,
                           format_func=lambda x: f"{x} · {cands[x]['full_name']} · {cands[x]['major']} · {st_map[x]}")
        status = st_map[cid]
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

        left, right = st.columns([0.55, 0.45], gap="large")
        with left:
            st.markdown("### Interview kit")
            st.caption(f"30-minute structured interview · AI draft ({kit_src})")
            if kit_json.get("focus_summary"):
                st.markdown(f"<div class='muted'>{ui.esc(kit_json['focus_summary'])}</div>", unsafe_allow_html=True)
            for i, q in enumerate(kit_json.get("questions", []), start=1):
                tag = "Probe a gap" if q["purpose"] == "probe_gap" else "Verify evidence"
                probes = " · ".join(q.get("follow_up_probes", []))
                signals = " · ".join(q.get("good_answer_signals", []))
                st.markdown(
                    f"<div class='card'><div class='label'>Q{i} · {tag} · "
                    f"{ui.esc(crit_names.get(q['criterion_id'], q['criterion_id'].replace('_', ' ')))}</div>"
                    f"<b>{ui.esc(q['question'])}</b>"
                    + (f"<div class='muted' style='margin-top:6px'>Follow up: {ui.esc(probes)}</div>" if probes else "")
                    + (f"<div class='muted'>Listen for: {ui.esc(signals)}</div>" if signals else "")
                    + "</div>", unsafe_allow_html=True)
        with right:
            st.markdown("### Invitation")
            st.caption("The AI never saw the name. It wrote a placeholder that the app filled in.")
            if inv_sent:
                ui.note(f"Sent (simulated) · {ui.esc(inv_json.get('slot', ''))}", "good")
                st.text_input("Subject", inv_json.get("subject_final", inv_json["subject"]), disabled=True)
                st.text_area("Body", inv_text, height=300, disabled=True)
                if status == "Invited":
                    if st.button("Mark interview scheduled", disabled=not is_rec):
                        with session_scope() as s:
                            confirm_interview(s, cid, ui.role())
                        st.rerun()
                else:
                    st.caption("Interview scheduled.")
            else:
                subject = st.text_input("Subject", inv_json["subject"], key=f"subj_{cid}")
                body = st.text_area("Body", inv_text, height=300, key=f"body_{cid}")
                slot = st.selectbox("Interview slot", [x for x in INTERVIEW_SLOTS if x not in taken], key=f"slot_{cid}")
                if st.button("Send invitation", type="primary", disabled=not is_rec):
                    try:
                        with session_scope() as s:
                            send_invite(s, cid, subject, body, slot, ui.role())
                        st.rerun()
                    except WorkflowError as exc:
                        ui.note(ui.esc(exc), "bad")

with tab_reg:
    to_send = sorted(cid for cid, v in st_map.items() if v in ("Rejected", "Ineligible"))
    sent = sum(v == "Regret Sent" for v in st_map.values())
    m = st.columns(2)
    m[0].metric("To send", len(to_send))
    m[1].metric("Sent", sent)
    st.caption("No AI here on purpose: every unsuccessful applicant gets the same respectful message, and scores are "
               "never disclosed.")
    st.markdown(f"<div class='card'><b>{ui.esc(REGRET_SUBJECT)}</b><div class='cv' style='border:none;padding:8px 0 0 0'>"
                f"{ui.esc(REGRET_BODY.format(first_name='[first name]'))}</div></div>", unsafe_allow_html=True)
    if st.button(f"Send {len(to_send)} regret emails", type="primary", disabled=not is_rec or not to_send):
        try:
            with session_scope() as s:
                for cid in to_send:
                    ensure_regret(s, cid)
                send_regrets(s, ui.role())
            st.rerun()
        except WorkflowError as exc:
            ui.note(ui.esc(exc), "bad")
    on_hold = sorted(cid for cid, v in st_map.items() if v == "On Hold")
    if on_hold:
        st.caption(f"On hold, no email yet: {', '.join(on_hold)}")
