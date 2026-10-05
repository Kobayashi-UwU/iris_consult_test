import streamlit as st

import ui

st.markdown("<div class='eyebrow'>Graduate Engineer Programme 2027</div>", unsafe_allow_html=True)
st.markdown("# Read every application. Keep people in charge.")
st.markdown(
    "<div class='lede'>An AI-assisted screening workflow for Thara Energy's graduate programme. It reads every CV "
    "against criteria the hiring manager approved, shows the evidence behind each score, and stops at two human "
    "approval gates before anything reaches a candidate.</div>",
    unsafe_allow_html=True,
)

c = st.columns(3)
c[0].metric("Applications per year", "5,000")
c[1].metric("Places", "80")
c[2].metric("Screening today", "Manual, weeks")

st.markdown("## How it works")
st.graphviz_chart("""
digraph G {
  rankdir=LR; bgcolor="transparent"; nodesep=0.25; ranksep=0.35;
  node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=10 color="#d6d6d0" fillcolor="#ffffff" fontcolor="#1c2b30" margin="0.18,0.1"];
  edge [color="#a9b2b4" arrowsize=0.6];
  a [label="Job description"];
  b [label="AI drafts\\nsuccess profile"];
  c [label="Hiring Manager\\napproves" color="#1f6f78" penwidth=1.6];
  d [label="Rules, redaction,\\nAI evidence, scoring"];
  e [label="Recruiter\\ndecides" color="#1f6f78" penwidth=1.6];
  f [label="Interview kit\\nand outreach"];
  g [label="Tracker, fairness,\\naudit log"];
  a -> b -> c -> d -> e -> f -> g;
}
""", width="stretch")
st.caption("Outlined steps are human approval gates. The workflow cannot continue past them without the right role.")

st.markdown("## Guided demo")
st.markdown("<div class='lede'>About five minutes. Switch role in the sidebar when a step asks for it.</div>",
            unsafe_allow_html=True)
done = ui.steps_done()
steps = [
    ("views/profile.py", "Success profile", "As Hiring Manager, load the AI draft, adjust a weight and approve."),
    ("views/screening.py", "Screening", "As Recruiter, run screening. Compare what the AI sees with the original CV."),
    ("views/review.py", "Shortlist review", "Open a scorecard, decide the borderline cases with a reason, confirm."),
    ("views/outreach.py", "Interview & outreach", "Review the interview kit, edit the invitation, send it (simulated)."),
    ("views/tracker.py", "Tracker", "Send regret emails, then check the funnel, fairness monitor and audit log."),
]
next_i = next((i for i, d in enumerate(done) if not d), None)
for i, (page, title, text) in enumerate(steps):
    status = "<span class='tag ok'>Done</span>" if done[i] else (
        "<span class='tag'>Next</span>" if i == next_i else "")
    left, right = st.columns([0.82, 0.18], vertical_alignment="center")
    left.markdown(f"<div class='card'><div class='label'>Step {i + 1}</div><b>{title}</b> {status}"
                  f"<div class='muted'>{text}</div></div>", unsafe_allow_html=True)
    right.page_link(page, label="Open")

with st.expander("Who does what"):
    st.markdown("""
| Task | Done by | Why |
|---|---|---|
| Turn the job description into criteria | AI drafts, Hiring Manager approves | Language task; the standard comes from a person |
| Eligibility knock-outs | Rules | Must be exact and checkable |
| Remove name, gender, age, university, contact details | Code | Guaranteed before any text reaches the AI |
| Find evidence in each CV for each criterion | AI | Reading at scale |
| Turn evidence levels into a score | Code | Repeatable; the AI never sees the weights |
| Decide who gets an interview | Recruiter | A decision about a person stays with a person |
| Interview questions and invitations | AI drafts, Recruiter edits and sends | Tailored writing; regret emails use one fixed template |
""")
