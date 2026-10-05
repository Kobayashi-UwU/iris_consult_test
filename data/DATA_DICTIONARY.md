# Data Dictionary

All data is synthetic, created for this case. Thara Energy is fictional. People are fictional, and their names are random combinations. University names are real Thai institutions, used only as realistic context. Companies in the CVs are fictional. Email addresses use `example.com`.

## Files

| File | What it is |
|---|---|
| `candidates.csv` | Applicant database export: 40 applications to the Graduate Engineer Programme 2027 (one row per applicant) |
| `cv/C-0xx.txt` | Each applicant's CV as plain text (also embedded in `candidates.csv` → `cv_text`) |
| `job_description.md` | The job description the hiring manager provides |
| `success_profile_v1.json` | The AI-drafted success profile (criteria, weights, anchors, knock-outs) before hiring-manager edits |
| `ground_truth.csv` | Planted test labels for checking the prototype. **Never shown to the AI or used in scoring** |
| `cache/*.json` | Precomputed Gemini outputs used by Demo mode, keyed by prompt version + input hash |
| App database (`candidate_status`, `evaluations`, `decisions`, `communications`, `audit_log`) | The candidate tracker and workflow state. Export from the *Tracker* and *Audit* pages |

## `candidates.csv`

"Sent to AI" means whether the field can reach Gemini. "No" fields are removed by redaction or never included in a prompt.

| Field | Type | Description | Example | PII / protected | Sent to AI |
|---|---|---|---|---|---|
| `candidate_id` | string | Unique applicant ID | `C-004` | No | No (not needed) |
| `full_name` | string | Fictional full name (romanised Thai) | `Supaporn Khamwong` | **PII** | No (redacted) |
| `email` | string | Fake email on example.com | `supaporn.kha04@example.com` | **PII** | No |
| `phone` | string | Fake Thai mobile number | `088-648-5676` | **PII** | No |
| `gender` | enum | Female / Male | `Female` | **Protected** | No. Used only for fairness monitoring |
| `date_of_birth` | date | YYYY-MM-DD | `2003-02-09` | **PII / protected (age)** | No |
| `university` | string | Awarding university | `Rajamangala University of Technology Isan` | Proxy risk | No (redacted) |
| `university_tier` | enum | Tier 1 / Tier 2 / Regional. Mock grouping for fairness monitoring only, not a quality judgement | `Regional` | Proxy risk | No |
| `region` | enum | Home region: Bangkok & Metro, Central, North, Northeast, East, South | `Northeast` | Proxy risk | No |
| `degree` | enum | B.Eng. / M.Eng. | `B.Eng.` | No | Yes (in CV) |
| `major` | string | Engineering discipline | `Chemical Engineering` | No | Knock-out rule only |
| `graduation_year` | int | Year graduated or expected | `2026` | No | Knock-out rule only |
| `gpa` | float | Cumulative GPA (0–4) | `2.78` | No | In CV text. Optional knock-out, off by default |
| `english_test` | enum | TOEIC / IELTS | `TOEIC` | No | In CV text |
| `english_score` | string | Test score | `610` | No | In CV text |
| `willing_offshore` | Yes/No | Willing to work on rotation, including offshore | `Yes` | No | Knock-out rule only |
| `right_to_work_th` | Yes/No | Has the right to work in Thailand | `Yes` | No | Knock-out rule only |
| `application_date` | date | Date applied | `2026-08-19` | No | No |
| `source_channel` | enum | Careers site, University career fair, LinkedIn, Employee referral, JobThai | `Careers site` | No | No |
| `cv_text` | text | Full CV | — | Contains PII | **Only after redaction** |

In the app database, `gender`, `region` and `university_tier` live in a separate `candidate_demographics` table. Only the fairness monitor reads it.

## `ground_truth.csv`

| Field | Description |
|---|---|
| `archetype` | Planted profile: Strong (8), Mid (12), Weak (9), Hidden gem (3), Keyword stuffer (2), Career pivot (2), Borderline (2), Ineligible (1), Prompt injection (1) |
| `expected_outcome` | What a good process should do with this applicant |
| `brief` | The brief the synthetic CV was written from |

The planted cases test specific behaviour:
- **Hidden gem:** strong practical evidence, moderate GPA, less-known university. Should be surfaced.
- **Keyword stuffer:** many buzzwords, no evidence. Should not be proposed.
- **Prompt injection:** the CV contains text telling the screening AI to give the maximum score. Should be flagged, then scored on its real evidence.
- **Ineligible:** graduated in 2022, outside the 2025–2027 window. Should be knocked out by a rule, with the reason.

## Workflow tables (app database)

| Table | Key fields |
|---|---|
| `success_profiles` | `version`, `status` (approved/superseded), `profile_json`, `approved_by`, `approved_at` |
| `evaluations` | `candidate_id`, `profile_version`, `knockout_pass`, `knockout_reasons`, `assessments` (per criterion: level 0–3, evidence quotes, quote verified, rationale, gaps, points), `total_score`, `flags`, `bucket` (Proposed / Needs Review / Not Proposed / Ineligible), `rank`, `prompt_version`, `model`, `input_hash`, `source` (live / cache) |
| `decisions` | `system_bucket`, `human_decision` (Approved / Rejected / On Hold), `is_override`, `reason_code`, `reason_text`, `decided_by` |
| `candidate_status` | Current status: Applied → Ineligible / Proposed / Needs Review / Not Proposed → Approved / Rejected / On Hold → Invited → Interview Scheduled, or Regret Sent |
| `communications` | `kind` (interview_kit / invite / regret), `draft_json`, `edited_text`, `sent` |
| `audit_log` | Append-only: `ts`, `actor_type` (ai / system / human), `actor`, `action`, `candidate_id`, `before`, `after`, `reason`, `profile_version`, `prompt_version`, `input_hash` |
