"""Deterministic keyword-heuristic stand-in for Gemini.

Used ONLY by unit tests and for local UI development without an API key
(`python scripts/build_cache.py --stub` into a scratch CACHE_DIR). Results are
labelled model="stub-heuristic" and must never be shipped as the demo cache.
"""
import json
import re

from core.schemas import CandidateEvaluation, EmailDraft, InterviewKit, SuccessProfile

STUB_MODEL = "stub-heuristic"


def _anchors(name: str) -> dict:
    return {
        "level_0": f"No evidence of {name.lower()}.",
        "level_1": f"Mentions {name.lower()} without a concrete example.",
        "level_2": f"Clear, relevant example of {name.lower()}.",
        "level_3": f"Specific, high-impact example of {name.lower()} with outcomes.",
    }


DEFAULT_PROFILE = {
    "role_summary": "Graduates with a strong technical foundation, hands-on engineering experience and a safety mindset, "
                    "who use data to solve problems and want to help lead the energy transition.",
    "knockouts": [
        {"id": "eligible_major", "field": "major", "operator": "in",
         "values": ["Petroleum Engineering", "Chemical Engineering", "Mechanical Engineering", "Electrical Engineering",
                    "Instrumentation & Control Engineering", "Environmental Engineering", "Computer Engineering",
                    "Industrial Engineering", "Energy Engineering"],
         "description": "Degree in an eligible engineering discipline", "enabled": True},
        {"id": "graduation_window", "field": "graduation_year", "operator": "between", "values": ["2025", "2027"],
         "description": "Graduated or graduating between 2025 and 2027", "enabled": True},
        {"id": "rotation_offshore", "field": "willing_offshore", "operator": "equals", "values": ["Yes"],
         "description": "Willing to work on rotation including offshore", "enabled": True},
        {"id": "right_to_work", "field": "right_to_work_th", "operator": "equals", "values": ["Yes"],
         "description": "Right to work in Thailand", "enabled": True},
    ],
    "criteria": [
        {"id": "technical_foundation", "name": "Technical foundation", "weight": 25,
         "definition": "Depth of discipline knowledge shown through coursework, thesis or senior project.",
         "anchors": _anchors("Technical foundation")},
        {"id": "applied_experience", "name": "Applied engineering experience", "weight": 20,
         "definition": "Internships, co-op, plant or field work and practical projects.",
         "anchors": _anchors("Applied engineering experience")},
        {"id": "safety_mindset", "name": "Safety & operational mindset", "weight": 15,
         "definition": "Awareness of hazards and safe work practices.", "anchors": _anchors("Safety mindset")},
        {"id": "data_problem_solving", "name": "Problem solving & data/digital", "weight": 15,
         "definition": "Using data and digital tools to analyse and solve problems.",
         "anchors": _anchors("Data problem solving")},
        {"id": "energy_transition", "name": "Energy transition orientation", "weight": 10,
         "definition": "Interest and evidence related to CCS, hydrogen, renewables or efficiency.",
         "anchors": _anchors("Energy transition orientation")},
        {"id": "collaboration_leadership", "name": "Collaboration & leadership", "weight": 10,
         "definition": "Teamwork and taking responsibility.", "anchors": _anchors("Collaboration and leadership")},
        {"id": "communication", "name": "Communication", "weight": 5,
         "definition": "Clear communication in English and Thai.", "anchors": _anchors("Communication")},
    ],
}

KEYWORDS = {
    "technical_foundation": ["thesis", "senior project", "simulation", "model", "design", "analysis"],
    "applied_experience": ["intern", "co-op", "cooperative", "plant", "site", "field", "offshore", "overhaul"],
    "safety_mindset": ["safety", "permit", "hse", "lock-out", "confined", "h2s", "hazop", "near-miss"],
    "data_problem_solving": ["python", "data", "model", "analys", "matlab", "script", "dashboard"],
    "energy_transition": ["carbon", "hydrogen", "solar", "renewable", "co2", "ccs", "biomass", "emission"],
    "collaboration_leadership": ["led", "lead", "team", "president", "captain", "mentor", "volunteer"],
    "communication": ["toeic", "ielts", "present", "english", "teaching"],
}


def _between(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", text, re.DOTALL)
    return m.group(1) if m else ""


def _evaluate(user: str) -> dict:
    criteria = json.loads(_between(user, "criteria"))
    cv = _between(user, "cv")
    lines = [ln.strip(" -•\t") for ln in cv.splitlines() if len(ln.split()) >= 4]
    out = []
    for c in criteria:
        kws = KEYWORDS.get(c["id"], [c["name"].split()[0].lower()])
        hits = [ln for ln in lines if any(k in ln.lower() for k in kws)]
        quant = [ln for ln in hits if re.search(r"\d", ln)]
        level = 0 if not hits else (3 if len(quant) >= 2 else 2 if quant or len(hits) >= 2 else 1)
        quotes = [" ".join(ln.split()[:20]) for ln in (quant or hits)[:2]]
        out.append({"criterion_id": c["id"], "level": level, "evidence_quotes": quotes,
                    "rationale": f"Stub heuristic: {len(hits)} relevant line(s), {len(quant)} with numbers.",
                    "gaps": [] if level == 3 else [f"More specific evidence of {c['name'].lower()}"]})
    sus = bool(re.search(r"ignore (all )?previous instructions", cv, re.IGNORECASE))
    return {"assessments": out, "suspicious_content": sus,
            "suspicious_content_note": "Instruction aimed at the screening system." if sus else "",
            "summary": "Stub summary: strengths and gaps estimated by keyword heuristics."}


def stub_generate(system: str, user: str, schema) -> dict:
    if schema is SuccessProfile:
        return SuccessProfile.model_validate(DEFAULT_PROFILE).model_dump()
    if schema is CandidateEvaluation:
        return CandidateEvaluation.model_validate(_evaluate(user)).model_dump()
    if schema is InterviewKit:
        crit = json.loads(_between(user, "criteria"))
        qs = [{"criterion_id": c["id"], "purpose": "probe_gap" if i % 2 else "verify_evidence",
               "question": f"Tell me about a time you demonstrated {c['name'].lower()}.",
               "follow_up_probes": ["What was your personal role?"], "good_answer_signals": ["Specific example", "Clear outcome"]}
              for i, c in enumerate(crit[:5])]
        return InterviewKit.model_validate({"focus_summary": "Stub interview kit.", "questions": qs}).model_dump()
    if schema is EmailDraft:
        return {"subject": "Interview invitation – Thara Energy Graduate Engineer Programme 2027",
                "body": "Dear {{first_name}},\n\nThank you for your application. We would like to invite you to a "
                        "30-minute video interview on {{interview_slot}}. Please reply to confirm.\n\n"
                        "Talent Acquisition Team, Thara Energy"}
    raise ValueError(schema)
