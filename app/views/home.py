import streamlit as st

import ui

st.markdown("<div class='eyebrow'>Thara Energy · Graduate Engineer Programme 2027</div>", unsafe_allow_html=True)
st.markdown("# Shortlist graduate engineers in minutes, with people making every decision")
st.markdown(
    "<div class='lede'>40 applications are waiting. The AI reads every CV and shows its evidence; the Hiring Manager "
    "and the Recruiter approve each step. Follow the button below. Each page tells you what to do next.</div>",
    unsafe_allow_html=True,
)

p = ui.progress_state()
nxt = ui.next_action(p)
done = ui.steps_done(p)
started = any(done)

st.markdown(
    f"<div class='hero'><div class='label muted'>{'Next step' if started else 'Start here'}</div>"
    f"<div class='big'>Step {nxt['step']}: {nxt['label']}</div><div class='muted'>{nxt['why']}</div></div>",
    unsafe_allow_html=True,
)
ui.go("Start the demo" if not started else f"Continue: {nxt['label']}", nxt["page"], key="home_cta")

st.write("")
st.markdown("## The five steps")
who = ["Hiring Manager", "System, then you check", "Recruiter", "Recruiter", "Anyone"]
what = [
    "Approve what a strong graduate looks like: six criteria, weights and eligibility rules drafted by the AI.",
    "Screen all 40 applications. Personal details are removed before the AI reads anything.",
    "Approve the proposed shortlist and decide the borderline cases, with a reason.",
    "Send interview invitations and polite replies to everyone else.",
    "See the funnel, the fairness check and a full audit trail.",
]
for i, (title, w, text) in enumerate(zip(ui.STEPS, who, what)):
    status = "<span class='tag ok'>Done</span>" if done[i] else ("<span class='tag'>Next</span>" if i + 1 == nxt["step"] else "")
    st.markdown(f"<div class='card'><div class='label muted'>Step {i + 1} · {w}</div><b>{title}</b> {status}"
                f"<div class='muted'>{text}</div></div>", unsafe_allow_html=True)

with st.expander("How the AI is kept in check"):
    st.markdown("""
| Task | Done by | Why |
|---|---|---|
| Turn the job description into criteria | AI drafts, Hiring Manager approves | The standard comes from a person |
| Eligibility rules | Code | Must be exact and checkable |
| Remove name, gender, age, university, contact details | Code | Guaranteed before any text reaches the AI |
| Find evidence in each CV | AI | Reading at scale; every quote is checked against the CV |
| Turn evidence into a score | Code | Repeatable; the AI never sees the weights |
| Decide who is interviewed | Recruiter | A decision about a person stays with a person |
| Interview questions and invitations | AI drafts, Recruiter sends | Tailored writing; replies to unsuccessful applicants use one fixed template |
""")
