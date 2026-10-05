# Thara Energy · Graduate Engineer Recruiting Agent

Prototype for the IRIS P&O Digital, Data & AI case assessment (**Option B: Agentic Recruiting Workflow**).

An AI-assisted screening workflow for the Graduate Engineer Programme (about 5,000 applications for 80 places). It reads every CV against criteria the hiring manager approved, shows the evidence behind every score, and stops at two human approval gates. Every AI output and human decision is logged.

```
JD → [AI] success profile → HUMAN GATE 1 (Hiring Manager approves)
   → [rules] knock-outs → [code] remove PII → [AI] evidence per criterion → [code] verify quotes + score + band
   → HUMAN GATE 2 (Recruiter approves / overrides with reason)
   → [AI] interview kit + invitation (human edits and sends) → tracker, funnel, fairness monitor, audit log
```

- **Requirements:** [requirement.md](requirement.md)
- **Architecture and tech stack:** [techstack.md](techstack.md)
- **Data dictionary:** [data/DATA_DICTIONARY.md](data/DATA_DICTIONARY.md)

## Try it
Open the deployed link. It starts in **Demo mode**, which replays AI results generated with Gemini in advance, so no API key is needed. Follow the *Guided demo* on the Home page. Use the sidebar to switch between **Hiring Manager** and **Recruiter**, and to **Reset demo**.

## Run locally
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # optional: add GEMINI_API_KEY for Live mode
streamlit run app/streamlit_app.py
```
The app creates a local SQLite database (`local.db`) and loads the 40 mock candidates on first start.

### Rebuild data and AI cache (optional)
```bash
python scripts/generate_mock_data.py   # data/cv/*.txt + specs -> candidates.csv, ground_truth.csv
python scripts/build_cache.py          # needs GEMINI_API_KEY; ~120 Gemini calls, resumable
pytest                                 # unit + end-to-end workflow tests (Gemini replaced by a stub)
```

## Deploy on Railway
1. Push this repo to GitHub, then in Railway choose **New Project → Deploy from GitHub repo**.
2. **Add → Database → PostgreSQL**, and reference its `DATABASE_URL` in the app service.
3. Set Variables: `APP_MODE=demo`. Optionally add `GEMINI_API_KEY` and `GEMINI_MODEL` to enable the Live toggle.
4. The start command and healthcheck come from `railway.json`. Then **Settings → Networking → Generate Domain**.

## AI tools used
| Tool | Used for |
|---|---|
| Claude Code (Claude Opus) | Pair-programming the app, tests and documents. Writing the 40 synthetic CVs from structured specs |
| Google Gemini (via AI Studio API) | The runtime agents: success-profile drafting, evidence extraction, interview kits, invitation emails |
