"""Shared UI helpers: boot, sidebar (role + AI mode + reset), stepper, badges, chart colours."""
import streamlit as st

from ai.client import LLMClient
from core import config
from core.db import get_state, session_scope
from core.seed import ensure_seeded, reset
from core.workflow import ROLE_HM, ROLE_RECRUITER, active_profile, is_confirmed, statuses

# Chart colours (validated reference palette: categorical slots 1-3 + status).
SERIES_1 = "#2a78d6"
SERIES_2 = "#eb6834"
SERIES_3 = "#1baf7a"
TRACK = "#d9d8d4"
GOOD = "#0ca30c"
CRITICAL = "#d03b3b"

BUCKET_BADGE = {
    "Proposed": ":green-badge[Proposed]",
    "Needs Review": ":orange-badge[Needs review]",
    "Not Proposed": ":gray-badge[Not proposed]",
    "Ineligible": ":red-badge[Ineligible]",
}
FLAG_SHORT = {
    "unverified_quote": "⚠ unverified quote",
    "unsupported_level": "⚠ level without quote",
    "missing_criterion": "⚠ missing criterion",
    "suspicious_content": "🛑 suspicious content",
    "pii_leak_blocked": "🛑 PII check failed",
    "ai_unavailable": "⚠ no AI result",
}


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
    return st.session_state["role"]


def client() -> LLMClient:
    return LLMClient(st.session_state["mode"])


def sidebar() -> None:
    with st.sidebar:
        st.markdown("### You are acting as")
        st.radio("Role", [ROLE_HM, ROLE_RECRUITER], key="role", label_visibility="collapsed",
                 help="No real login in this prototype. Each approval step only works for the right role.")
        st.markdown("### AI mode")
        live_ok = config.live_available()
        st.toggle("Live AI (Gemini)", key="_live_toggle", disabled=not live_ok,
                  help="Off = Demo mode: AI answers come from results precomputed with Gemini, so the "
                       "prototype works without an API key. On = calls Gemini live, falling back to the cache on errors.")
        st.session_state["mode"] = "live" if (st.session_state.get("_live_toggle") and live_ok) else "demo"
        if st.session_state["mode"] == "live":
            st.caption(f"🟢 Live · `{config.GEMINI_MODEL}`")
        else:
            st.caption("🔵 Demo · precomputed Gemini results" + ("" if live_ok else " (no API key configured)"))
        st.divider()
        with st.popover("Reset demo", width="stretch"):
            st.write("Clear all decisions, emails and logs and reload the 40 mock candidates.")
            if st.button("Yes, reset everything", type="primary"):
                reset()
                for k in list(st.session_state.keys()):
                    if k not in ("role", "mode", "_live_toggle"):
                        del st.session_state[k]
                st.rerun()
        st.caption("All data is synthetic. Thara Energy is a fictional company.")


def progress_state() -> dict:
    with session_scope() as s:
        st_map = statuses(s)
        prof = active_profile(s)
        return {
            "profile_version": prof.version if prof else None,
            "screening": get_state(s, "screening"),
            "confirmed": is_confirmed(s),
            "decided": sum(v in ("Approved", "Rejected", "On Hold", "Invited", "Interview Scheduled", "Regret Sent")
                           for v in st_map.values()),
            "invited": sum(v in ("Invited", "Interview Scheduled") for v in st_map.values()),
            "regrets": sum(v == "Regret Sent" for v in st_map.values()),
        }


STEPS = [
    ("1", "Success profile", "Hiring Manager approves"),
    ("2", "Screening", "AI + rules"),
    ("3", "Shortlist review", "Recruiter approves"),
    ("4", "Interview kit & outreach", "AI drafts, human sends"),
    ("5", "Tracker & audit", "Monitor"),
]


def stepper(current: int) -> None:
    p = progress_state()
    scr = p["screening"]
    done = [
        p["profile_version"] is not None,
        bool(scr) and not scr.get("stale"),
        p["confirmed"],
        p["invited"] > 0,
        p["invited"] > 0 and p["regrets"] > 0,
    ]
    cols = st.columns(len(STEPS))
    for i, (col, (n, title, sub)) in enumerate(zip(cols, STEPS)):
        mark = "✅" if done[i] else ("👉" if i + 1 == current else "○")
        weight = "**" if i + 1 == current else ""
        col.markdown(f"{mark} {weight}Step {n} · {title}{weight}  \n:gray[{sub}]")
    st.divider()


def flags_text(flags: list[str]) -> str:
    return ", ".join(FLAG_SHORT.get(f, f) for f in flags)
