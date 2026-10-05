import streamlit as st

import ui

st.title("Graduate Engineer Recruiting Agent")
st.caption("Thara Energy (fictional) · Graduate Engineer Programme 2027 · prototype by IRIS P&O Digital, Data & AI")

st.info('**CHRO:** "I want hiring that finds the right people faster, without losing human judgement. '
        'Show me something that works, not another report."', icon="💬")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Applications per year", "≈ 5,000")
c2.metric("Places", "80")
c3.metric("Manual CV screening", "Weeks")
c4.metric("Hiring-manager complaint", "Shortlists miss the right people")

st.subheader("What this prototype does")
st.markdown(
    "An **agentic screening workflow** that reads every CV against criteria the hiring manager approved, "
    "shows the **evidence behind every score**, and stops at **two human approval gates** before anything "
    "reaches a candidate. Every step is logged."
)

st.graphviz_chart("""
digraph G {
  rankdir=LR; bgcolor="transparent";
  node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=11 color="#9a9a96" fillcolor="#f4f4f2"];
  edge [color="#8a8a86"];
  jd   [label="Job description"];
  s1   [label="1 · AI drafts\\nsuccess profile" fillcolor="#dbe8fa"];
  h1   [label="HUMAN GATE 1\\nHiring Manager\\napproves criteria" shape=octagon fillcolor="#ffe3cc"];
  ko   [label="Rules:\\nknock-outs"];
  red  [label="Code:\\nremove PII"];
  ev   [label="2 · AI extracts\\nevidence per criterion" fillcolor="#dbe8fa"];
  sc   [label="Code: verify quotes,\\nscore, band"];
  h2   [label="HUMAN GATE 2\\nRecruiter approves /\\noverrides with reason" shape=octagon fillcolor="#ffe3cc"];
  s4   [label="4 · AI drafts interview kit\\n+ invite (human edits, sends)" fillcolor="#dbe8fa"];
  s5   [label="5 · Tracker, funnel,\\nfairness, audit log"];
  jd -> s1 -> h1 -> ko -> red -> ev -> sc -> h2 -> s4 -> s5;
}
""", width="stretch")

st.subheader("Who does what")
st.markdown("""
| Task | Done by | Why |
|---|---|---|
| Turn the job description into criteria | **AI** drafts → **Hiring Manager** approves | Language task, but the standard must come from a person |
| Eligibility knock-outs | **Rules** | Must be 100% checkable, no judgement |
| Remove name, gender, age, university, contact details | **Code** | Must be guaranteed before any text reaches the AI |
| Find evidence in each CV for each criterion | **AI** | Reading comprehension at scale |
| Turn evidence levels into a score | **Code** | Repeatable and explainable; the AI never sees the weights |
| Decide who gets an interview | **Recruiter** | A decision about a person stays with a person |
| Interview questions and invitation emails | **AI** drafts → **Recruiter** edits and sends | Tailored writing; regret emails use one fixed template instead |
""")

st.subheader("Guided demo (about 5 minutes)")
p = ui.progress_state()
scr = p["screening"]
steps = [
    (p["profile_version"] is not None, "views/profile.py",
     "Switch to **Hiring Manager**, open *Success Profile*, load the AI draft, adjust a weight and **approve** it."),
    (bool(scr) and not scr.get("stale"), "views/screening.py",
     "Switch to **Recruiter**, open *Screening* and **run** it. Look at *What the AI sees* and the comparison with a keyword screen."),
    (p["confirmed"], "views/review.py",
     "Open *Shortlist Review*: read a scorecard, decide the *Needs review* cases (a reason is required), "
     "approve the proposed candidates and **confirm the shortlist**."),
    (p["invited"] > 0, "views/outreach.py",
     "Open *Interview Kit & Outreach*: generate a kit and invitation, edit it, pick a slot and send (simulated)."),
    (p["invited"] > 0 and p["regrets"] > 0, "views/tracker.py",
     "Send the regret emails, then check *Tracker & Funnel* and *Fairness & Audit*."),
]
for i, (done, page, text) in enumerate(steps, start=1):
    c1, c2 = st.columns([0.8, 0.2])
    c1.markdown(f"{'✅' if done else '⬜'} **{i}.** {text}")
    c2.page_link(page, label="Open →")

mode = st.session_state["mode"]
st.caption(
    f"Current mode: **{'Live Gemini' if mode == 'live' else 'Demo'}**. Demo mode replays results that were "
    "generated with Gemini in advance, so the whole flow works without an API key. Use *Reset demo* in the "
    "sidebar to start again."
)
