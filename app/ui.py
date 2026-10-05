"""Shared UI: theme CSS, sidebar (role, AI mode, reset), page header, stepper, notes, chart styling."""
import html

import streamlit as st

from ai.client import LLMClient
from core import config
from core.db import get_state, session_scope
from core.seed import ensure_seeded, reset
from core.workflow import ROLE_HM, ROLE_RECRUITER, active_profile, is_confirmed, statuses

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
    st.session_state.setdefault("role", ROLE_RECRUITER)
    st.session_state.setdefault("mode", config.APP_MODE if config.live_available() else "demo")
    st.session_state.setdefault("blind", True)
    st.session_state.setdefault("_live_toggle", st.session_state["mode"] == "live")


def role() -> str:
    return st.session_state.get("role") or ROLE_RECRUITER


def client() -> LLMClient:
    return LLMClient(st.session_state["mode"])


def sidebar() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    with st.sidebar:
        st.markdown("**Thara Energy**  \n<span class='muted'>Graduate recruiting agent</span>", unsafe_allow_html=True)
        st.caption("Acting as")
        st.segmented_control("Role", [ROLE_HM, ROLE_RECRUITER], key="role", label_visibility="collapsed",
                             help="No real login in this prototype. Each approval only works for the right role.")
        live_ok = config.live_available()
        st.toggle("Live AI", key="_live_toggle", disabled=not live_ok,
                  help="Off: replays results generated with Gemini in advance, so the demo works without an API key. "
                       "On: calls the models live, falling back to saved results on errors.")
        st.session_state["mode"] = "live" if (st.session_state.get("_live_toggle") and live_ok) else "demo"
        st.caption("Calls Gemini, falls back to Gemma" if st.session_state["mode"] == "live"
                   else "Replaying saved AI results" + ("" if live_ok else " (no API key)"))
        with st.popover("Reset demo", width="stretch"):
            st.write("Clear all decisions, emails and logs, and reload the 40 mock applicants.")
            if st.button("Reset", type="primary"):
                reset()
                for k in list(st.session_state.keys()):
                    if k not in ("role", "mode", "_live_toggle"):
                        del st.session_state[k]
                st.rerun()
        st.caption("Synthetic data. Thara Energy is fictional.")


def progress_state() -> dict:
    with session_scope() as s:
        st_map = statuses(s)
        prof = active_profile(s)
        return {
            "profile_version": prof.version if prof else None,
            "screening": get_state(s, "screening"),
            "confirmed": is_confirmed(s),
            "invited": sum(v in ("Invited", "Interview Scheduled") for v in st_map.values()),
            "regrets": sum(v == "Regret Sent" for v in st_map.values()),
        }


STEPS = ["Success profile", "Screening", "Shortlist review", "Interview & outreach", "Tracker"]


def steps_done(p: dict | None = None) -> list[bool]:
    p = p or progress_state()
    scr = p["screening"]
    return [
        p["profile_version"] is not None,
        bool(scr) and not scr.get("stale"),
        p["confirmed"],
        p["invited"] > 0,
        p["invited"] > 0 and p["regrets"] > 0,
    ]


def header(title: str, lede: str = "", step: int | None = None) -> None:
    if step:
        st.markdown(f"<div class='eyebrow'>Step {step} of 5</div>", unsafe_allow_html=True)
    st.markdown(f"# {title}")
    if lede:
        st.markdown(f"<div class='lede'>{lede}</div>", unsafe_allow_html=True)
    if step:
        done = steps_done()
        items = []
        for i, label in enumerate(STEPS, start=1):
            cls = "current" if i == step else ("done" if done[i - 1] else "")
            items.append(f"<div class='step {cls}'><span class='n'>{i}</span>{label}</div>")
        st.markdown(f"<div class='stepper'>{''.join(items)}</div>", unsafe_allow_html=True)


def note(text: str, tone: str = "info") -> None:
    """Quiet callout. Callers escape user data with esc(); <b> and <i> are allowed."""
    st.markdown(f"<div class='note {tone}'>{text}</div>", unsafe_allow_html=True)


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
