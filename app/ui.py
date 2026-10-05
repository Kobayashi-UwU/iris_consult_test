"""Shared UI: theme CSS, sidebar (role, AI mode, reset), page header, stepper, notes, chart styling."""
import html

import streamlit as st

from ai.client import LLMClient
from core import config
from core.db import get_state, session_scope
from core.seed import ensure_seeded, reset
from core.workflow import active_profile, is_confirmed, statuses

# Palette
INK = "#1c2b30"
MUTED = "#66757a"
LINE = "#e4e4df"
ACCENT = "#1f6f78"
ACCENT_SOFT = "#e6f0f0"
WARN = "#a85a1b"
WARN_SOFT = "#fbf1e6"
BAD = "#b3372f"
GOOD = "#2f7d4f"
TRACK = "#e7e7e2"
SERIES_1 = ACCENT
CRITICAL = BAD

BUCKET_BADGE = {
    "Proposed": ":green-badge[Proposed]",
    "Needs Review": ":orange-badge[Needs review]",
    "Not Proposed": ":gray-badge[Not proposed]",
    "Ineligible": ":red-badge[Ineligible]",
}
FLAG_SHORT = {
    "unverified_quote": "Unverified quote",
    "unsupported_level": "Level without quote",
    "missing_criterion": "Missing criterion",
    "suspicious_content": "Suspicious content",
    "pii_leak_blocked": "PII check failed",
    "ai_unavailable": "No AI result",
}

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, .stMarkdown, button, input, textarea, select, [data-testid="stSidebar"] {{
  font-family: 'Inter', -apple-system, 'Segoe UI', sans-serif;
}}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stToolbar"], [data-testid="stDecoration"], footer {{ display: none !important; }}
.block-container {{ padding-top: 2.2rem; padding-bottom: 4rem; max-width: 1240px; }}
h1 {{ font-size: 1.9rem !important; font-weight: 650 !important; letter-spacing: -0.02em; }}
h2 {{ font-size: 1.3rem !important; font-weight: 600 !important; letter-spacing: -0.01em; }}
h3 {{ font-size: 1.05rem !important; font-weight: 600 !important; }}
[data-testid="stSidebar"] {{ background: #f4f4f0; border-right: 1px solid {LINE}; }}
[data-testid="stMetric"] {{ background: #fff; border: 1px solid {LINE}; border-radius: 10px; padding: 14px 16px; }}
[data-testid="stMetricLabel"] p {{ color: {MUTED}; font-size: 0.82rem; }}
[data-testid="stMetricValue"] {{ font-size: 1.6rem; font-weight: 600; }}
[data-testid="stExpander"] details {{ border: 1px solid {LINE}; border-radius: 10px; background: #fff; }}
.stButton button, .stDownloadButton button, .stFormSubmitButton button {{ border-radius: 8px; font-weight: 500; }}
.stButton button[kind="primary"] {{ padding: 0.55rem 1.2rem; font-weight: 600; }}
.hero {{ background: #fff; border: 1px solid {LINE}; border-radius: 14px; padding: 22px 24px; margin: 6px 0 14px 0; }}
.hero .big {{ font-size: 1.25rem; font-weight: 600; margin: 2px 0 4px 0; }}
.label {{ font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em; }}
.eyebrow {{ color: {ACCENT}; font-size: 0.78rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; }}
.lede {{ color: {MUTED}; font-size: 0.98rem; margin: 2px 0 18px 0; max-width: 780px; }}
.stepper {{ display: flex; gap: 6px; margin: 4px 0 28px 0; }}
.step {{ flex: 1; padding-top: 10px; border-top: 3px solid {TRACK}; font-size: 0.82rem; color: {MUTED}; }}
.step.done {{ border-top-color: #8fbcc0; color: {INK}; }}
.step.current {{ border-top-color: {ACCENT}; color: {INK}; font-weight: 600; }}
.step .n {{ display: block; font-size: 0.72rem; color: {MUTED}; font-weight: 500; }}
.note {{ border-left: 3px solid {ACCENT}; background: {ACCENT_SOFT}; padding: 10px 14px; border-radius: 0 8px 8px 0;
        font-size: 0.92rem; color: {INK}; margin: 6px 0 14px 0; }}
.note.warn {{ border-left-color: {WARN}; background: {WARN_SOFT}; }}
.note.bad {{ border-left-color: {BAD}; background: #fbecea; }}
.note.good {{ border-left-color: {GOOD}; background: #eaf5ee; }}
.note.plain {{ border-left-color: {LINE}; background: #fff; color: {MUTED}; }}
.card {{ background: #fff; border: 1px solid {LINE}; border-radius: 10px; padding: 16px 18px; margin-bottom: 10px; }}
.card .label {{ color: {MUTED}; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 4px; }}
.muted {{ color: {MUTED}; }}
.tag {{ display: inline-block; font-size: 0.72rem; padding: 1px 8px; border-radius: 999px; border: 1px solid {LINE};
       color: {MUTED}; background: #fff; margin-right: 6px; }}
.tag.ok {{ color: {GOOD}; border-color: #bfdcc9; background: #f1f8f3; }}
.tag.no {{ color: {BAD}; border-color: #ecc5c1; background: #fdf3f2; }}
.quote {{ border-left: 2px solid {LINE}; padding: 2px 0 2px 10px; margin: 6px 0; font-size: 0.9rem; }}
.cv {{ white-space: pre-wrap; font-size: 0.84rem; line-height: 1.55; max-height: 640px; overflow-y: auto;
      background: #fff; border: 1px solid {LINE}; border-radius: 10px; padding: 16px; }}
.cv mark {{ background: #d8ebec; color: inherit; border-radius: 3px; padding: 0 2px; }}
</style>
"""


@st.cache_resource
def boot() -> bool:
    ensure_seeded()
    return True


def init_session() -> None:
    st.session_state.setdefault("mode", config.APP_MODE if config.live_available() else "demo")
    st.session_state.setdefault("blind", True)
    st.session_state.setdefault("_live_toggle", st.session_state["mode"] == "live")


def client() -> LLMClient:
    return LLMClient(st.session_state["mode"])


def sidebar() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    with st.sidebar:
        st.markdown("**Thara Energy**  \n<span class='muted'>Graduate recruiting agent</span>", unsafe_allow_html=True)
        st.caption("Each action button says who performs it: the Hiring Manager or the Recruiter. "
                   "Every action is logged.")
        live_ok = config.live_available()
        st.toggle("Live AI", key="_live_toggle", disabled=not live_ok,
                  help="Off: replays results generated with Gemini in advance, so the demo works without an API key. "
                       "On: calls the models live, falling back to saved results on errors.")
        st.session_state["mode"] = "live" if (st.session_state.get("_live_toggle") and live_ok) else "demo"
        st.caption("Calls Gemini, falls back to Gemma" if st.session_state["mode"] == "live"
                   else "Replaying saved AI results" + ("" if live_ok else " (no API key)"))
        with st.popover("Start over", width="stretch"):
            st.write("Clear all decisions, emails and logs, and reload the 40 mock applicants.")
            if st.button("Reset the demo", type="primary"):
                reset()
                for k in list(st.session_state.keys()):
                    if k not in ("mode", "_live_toggle"):
                        del st.session_state[k]
                st.switch_page("views/home.py")
        st.caption("Synthetic data. Thara Energy is fictional.")


def progress_state() -> dict:
    with session_scope() as s:
        st_map = statuses(s)
        prof = active_profile(s)
        return {
            "profile_version": prof.version if prof else None,
            "screening": get_state(s, "screening"),
            "confirmed": is_confirmed(s),
            "approved_waiting": sum(v == "Approved" for v in st_map.values()),
            "invited": sum(v in ("Invited", "Interview Scheduled") for v in st_map.values()),
            "regrets_pending": sum(v in ("Rejected", "Ineligible") for v in st_map.values()),
            "regrets": sum(v == "Regret Sent" for v in st_map.values()),
        }


STEPS = ["Success profile", "Screening", "Shortlist review", "Interview & outreach", "Tracker"]
PAGES = ["views/profile.py", "views/screening.py", "views/review.py", "views/outreach.py", "views/tracker.py"]


def steps_done(p: dict | None = None) -> list[bool]:
    p = p or progress_state()
    scr = p["screening"]
    outreach_done = p["confirmed"] and p["approved_waiting"] == 0 and p["regrets_pending"] == 0
    return [
        p["profile_version"] is not None,
        bool(scr) and not scr.get("stale"),
        p["confirmed"],
        outreach_done,
        outreach_done,
    ]


def next_action(p: dict | None = None) -> dict:
    """The one thing to do next, in plain words, and where to do it."""
    p = p or progress_state()
    scr = p["screening"]
    if p["profile_version"] is None:
        return {"step": 1, "label": "Approve the success profile", "page": PAGES[0],
                "why": "Agree what a strong graduate looks like before anyone is screened."}
    if not scr or scr.get("stale"):
        return {"step": 2, "label": "Screen the applications", "page": PAGES[1],
                "why": "Check all 40 applications against the approved profile."}
    if not p["confirmed"]:
        return {"step": 3, "label": "Review and confirm the shortlist", "page": PAGES[2],
                "why": "Decide the borderline cases and confirm who is invited to interview."}
    if p["approved_waiting"] or p["regrets_pending"]:
        return {"step": 4, "label": "Send invitations and replies", "page": PAGES[3],
                "why": "Invite the shortlisted applicants and let everyone else know."}
    return {"step": 5, "label": "See the results", "page": PAGES[4],
            "why": "Every applicant has an outcome. Review the funnel, fairness and audit trail."}


def go(label: str, page: str, primary: bool = True, key: str | None = None) -> None:
    if st.button(label, type="primary" if primary else "secondary", key=key or f"go_{page}_{label}"):
        st.switch_page(page)


def header(title: str, lede: str = "", step: int | None = None) -> None:
    if step:
        st.markdown(f"<div class='eyebrow'>Step {step} of 5</div>", unsafe_allow_html=True)
    st.markdown(f"# {title}")
    if lede:
        st.markdown(f"<div class='lede'>{lede}</div>", unsafe_allow_html=True)
    if step:
        p = progress_state()
        done = steps_done(p)
        items = []
        for i, label in enumerate(STEPS, start=1):
            cls = "current" if i == step else ("done" if done[i - 1] else "")
            items.append(f"<div class='step {cls}'><span class='n'>{i}</span>{label}</div>")
        st.markdown(f"<div class='stepper'>{''.join(items)}</div>", unsafe_allow_html=True)
        nxt = next_action(p)
        if nxt["step"] < step <= 4:
            note(f"This step opens after <b>{nxt['label'].lower()}</b>.", "plain")
            go(f"Go to step {nxt['step']}: {nxt['label']}", nxt["page"])
            st.stop()


def next_up(current_step: int) -> None:
    """Footer pointer to the next step once this one is finished."""
    nxt = next_action()
    if nxt["step"] > current_step:
        st.divider()
        st.markdown(f"<div class='label muted'>Next</div><b>{nxt['label']}</b><div class='muted'>{nxt['why']}</div>",
                    unsafe_allow_html=True)
        go(f"Continue to step {nxt['step']}", nxt["page"], key=f"next_{current_step}")


def note(text: str, tone: str = "info") -> None:
    """Quiet callout. Callers escape user data with esc(); <b> and <i> are allowed."""
    st.markdown(f"<div class='note {tone}'>{text}</div>", unsafe_allow_html=True)


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def esc(s) -> str:
    return html.escape(str(s))


def flags_text(flags: list[str]) -> str:
    return ", ".join(FLAG_SHORT.get(f, f) for f in flags)


def style_fig(fig, height: int):
    fig.update_layout(
        height=height, margin=dict(l=0, r=24, t=8, b=0), paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Inter, sans-serif", size=12, color=INK), showlegend=False,
    )
    fig.update_xaxes(showgrid=True, gridcolor="#ececE8", zeroline=False)
    fig.update_yaxes(showgrid=False)
    return fig
