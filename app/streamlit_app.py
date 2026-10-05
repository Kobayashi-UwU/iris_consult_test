"""Entry point: `streamlit run app/streamlit_app.py`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Thara Energy · Graduate Recruiting Agent", page_icon="⚙️", layout="wide")

pages = {
    "Overview": [st.Page("views/home.py", title="Home", icon="🏠", default=True)],
    "Workflow": [
        st.Page("views/profile.py", url_path="success-profile", title="1 · Success Profile", icon="🎯"),
        st.Page("views/screening.py", url_path="screening", title="2 · Screening", icon="🔎"),
        st.Page("views/review.py", url_path="shortlist-review", title="3 · Shortlist Review", icon="✅"),
        st.Page("views/outreach.py", url_path="outreach", title="4 · Interview Kit & Outreach", icon="✉️"),
    ],
    "Monitor": [
        st.Page("views/tracker.py", url_path="tracker", title="5 · Tracker & Funnel", icon="📊"),
        st.Page("views/fairness.py", url_path="fairness-audit", title="Fairness & Audit", icon="⚖️"),
    ],
}
pg = st.navigation(pages)

import ui  # noqa: E402

ui.boot()
ui.init_session()
ui.sidebar()
pg.run()
