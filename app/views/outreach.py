import streamlit as st

import ui
from ai.client import LLMUnavailable
from core.db import session_scope
from core.pipeline import (INTERVIEW_SLOTS, booked_slots, candidate_dicts, confirm_interview, ensure_interview_kit,
                           ensure_invite, ensure_regret, get_comm, send_invite, send_regrets)
from core.templates import REGRET_BODY, REGRET_SUBJECT
from core.workflow import ROLE_RECRUITER, WorkflowError, can_generate, statuses

st.title("Step 4 · Interview Kit & Outreach")
st.caption("The AI drafts a tailored interview kit and invitation for each approved candidate. "
           "The Recruiter edits and sends. Emails are simulated, nothing leaves the app.")
ui.stepper(4)

with session_scope() as s:
    ok, why = can_generate(s)
    if not ok:
        st.info(why, icon="🔒")
        st.stop()
    st_map = statuses(s)
    cands = {c["candidate_id"]: c for c in candidate_dicts(s)}

is_rec = ui.role() == ROLE_RECRUITER
if not is_rec:
    st.warning("Only the **Recruiter** can send emails. Switch role in the sidebar.", icon="🔒")

tab_inv, tab_reg = st.tabs(["Invitations & interview kits", "Regret emails"])

with tab_inv:
    approved = sorted(cid for cid, v in st_map.items() if v in ("Approved", "Invited", "Interview Scheduled"))
    if not approved:
        st.info("No approved candidates.")
    else:
        cid = st.selectbox(
            "Approved candidate", approved,
            format_func=lambda x: f"{x} · {cands[x]['full_name']} · {cands[x]['major']} · {st_map[x]}",
        )
        status = st_map[cid]
        try:
            with session_scope() as s:
                kit = ensure_interview_kit(s, ui.client(), cid)
                inv = ensure_invite(s, ui.client(), cid)
                kit_json, kit_src = kit.draft_json, kit.source
                inv_json, inv_text, inv_sent, inv_src = inv.draft_json, inv.edited_text, inv.sent, inv.source
                taken = booked_slots(s)
        except (LLMUnavailable, WorkflowError) as exc:
            st.error(str(exc))
            st.stop()

        k, e = st.columns([0.55, 0.45])
        with k:
            st.markdown(f"#### Interview kit · {cands[cid]['full_name']}")
            st.caption(f"AI draft ({kit_src}). 30-minute structured interview focused on this candidate's gaps.")
            st.markdown(f"**Focus:** {kit_json.get('focus_summary', '')}")
            for i, q in enumerate(kit_json.get("questions", []), start=1):
                tag = "🔍 Probe a gap" if q["purpose"] == "probe_gap" else "✔️ Verify evidence"
                with st.container(border=True):
                    st.markdown(f"**Q{i}. {q['question']}**  \n:gray[{tag} · criterion `{q['criterion_id']}`]")
                    if q.get("follow_up_probes"):
                        st.markdown("Follow-ups: " + " · ".join(q["follow_up_probes"]))
                    if q.get("good_answer_signals"):
                        st.markdown("Listen for: " + " · ".join(q["good_answer_signals"]))
        with e:
            st.markdown("#### Invitation email")
            st.caption(f"AI draft ({inv_src}). The AI never saw the name: it wrote a placeholder and the app filled it in.")
            if inv_sent:
                st.success(f"Sent (simulated) · slot: {inv_json.get('slot', '')}", icon="📨")
                st.text_input("Subject", inv_json.get("subject_final", inv_json["subject"]), disabled=True)
                st.text_area("Body", inv_text, height=300, disabled=True)
                if status == "Invited":
                    if st.button("Candidate confirmed the slot → mark interview scheduled", disabled=not is_rec):
                        with session_scope() as s:
                            confirm_interview(s, cid, ui.role())
                        st.rerun()
                else:
                    st.info("Interview scheduled.", icon="📅")
            else:
                subject = st.text_input("Subject", inv_json["subject"], key=f"subj_{cid}")
                body = st.text_area("Body (edit freely)", inv_text, height=300, key=f"body_{cid}")
                free = [x for x in INTERVIEW_SLOTS if x not in taken]
                slot = st.selectbox("Interview slot (mock calendar)", free, key=f"slot_{cid}")
                if st.button("📨 Send invitation (simulated)", type="primary", disabled=not is_rec):
                    try:
                        with session_scope() as s:
                            send_invite(s, cid, subject, body, slot, ui.role())
                        st.rerun()
                    except WorkflowError as exc:
                        st.error(str(exc))

with tab_reg:
    to_send = sorted(cid for cid, v in st_map.items() if v in ("Rejected", "Ineligible"))
    sent = sum(v == "Regret Sent" for v in st_map.values())
    st.markdown(f"**{len(to_send)}** to send · **{sent}** already sent")
    st.caption("No AI here on purpose: every unsuccessful applicant receives the same respectful, consistent message, "
               "and the email never reveals scores.")
    with st.container(border=True):
        st.markdown(f"**{REGRET_SUBJECT}**")
        st.text(REGRET_BODY.format(first_name="{first name}"))
    if st.button(f"📨 Send {len(to_send)} regret emails (simulated)", type="primary",
                 disabled=not is_rec or not to_send):
        try:
            with session_scope() as s:
                for cid in to_send:
                    ensure_regret(s, cid)
                send_regrets(s, ui.role())
            st.rerun()
        except WorkflowError as exc:
            st.error(str(exc))
    on_hold = sorted(cid for cid, v in st_map.items() if v == "On Hold")
    if on_hold:
        st.caption(f"On hold (no email yet): {', '.join(on_hold)}")
