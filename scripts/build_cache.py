"""Precompute every AI result the demo needs, using Gemini, and save it to data/cache.

Run once with GEMINI_API_KEY set (about 120 calls; resumable: results already in
the cache are reused). Then commit data/cache so the deployed demo works without a key.

    python scripts/build_cache.py            # real Gemini
    python scripts/build_cache.py --stub     # local dev only; set CACHE_DIR to a scratch folder
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT))

from ai import agents  # noqa: E402
from ai.client import CACHE, LLMClient, load_prompt  # noqa: E402
from core import config  # noqa: E402
from core.redaction import pii_leaks, redact  # noqa: E402
from core.schemas import CandidateEvaluation  # noqa: E402
from core.scoring import enrich_assessments, total_score  # noqa: E402
from core.seed import jd_text, read_candidates_csv  # noqa: E402

CONSISTENCY_SAMPLE = ["C-001", "C-004", "C-005", "C-017", "C-025"]


def patient(fn, *args, tries: int = 6, wait_s: int = 45):
    """Outer retry for long 'model overloaded' (503) spells; the client already retries short blips."""
    for i in range(tries):
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001
            if i == tries - 1:
                raise
            print(f"  retry {i + 1}/{tries - 1} in {wait_s}s: {str(exc)[:90]}", flush=True)
            time.sleep(wait_s)


def make_client(stub: bool) -> LLMClient:
    client = LLMClient("live")
    if stub:
        from tests.stub_llm import STUB_MODEL, stub_call
        client.mode, client.model, client.models = "live", STUB_MODEL, [STUB_MODEL]
        client._call = stub_call
        if config.CACHE_DIR.resolve() == (config.DATA_DIR / "cache").resolve():
            sys.exit("Refusing to write stub results into data/cache. Set CACHE_DIR to a scratch folder.")
    elif client.mode != "live":
        sys.exit("GEMINI_API_KEY is not set. Add it to .env or the environment.")
    client.prefer_cache = True
    return client


def parallel(fn, items, workers):
    out, done = [], 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for r in pool.map(fn, items):
            out.append(r)
            done += 1
            CACHE.save()
            print(f"  {done}/{len(items)}", flush=True)
    CACHE.save()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stub", action="store_true")
    ap.add_argument("--skip-consistency", action="store_true")
    ap.add_argument("--kits-top", type=int, default=20,
                    help="Precompute interview kits and invites for the N highest-scoring candidates (Live mode covers the rest)")
    args = ap.parse_args()
    client = make_client(args.stub)
    workers = 1 if args.stub else config.LLM_CONCURRENCY
    t0 = time.time()

    print(f"Model chain: {' → '.join(client.models)} · cache: {config.CACHE_DIR}")
    print("1/4 Success profile")
    profile, res = patient(agents.build_profile, client, jd_text())
    CACHE.save()
    if not args.stub:
        (config.DATA_DIR / "success_profile_v1.json").write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  {len(profile['criteria'])} criteria, weights {[c['weight'] for c in profile['criteria']]} ({res.source}, {res.model})")

    cands = read_candidates_csv()
    redacted = {}
    for c in cands:
        red, _ = redact(c["cv_text"], c)
        if pii_leaks(red, c):
            sys.exit(f"PII leak in {c['candidate_id']}; fix redaction before calling the API")
        redacted[c["candidate_id"]] = red

    print(f"2/4 Evidence extraction for {len(cands)} CVs")

    def evidence(c):
        out, res = patient(agents.extract_evidence, client, redacted[c["candidate_id"]], profile)
        enriched, flags = enrich_assessments(out["assessments"], profile["criteria"], redacted[c["candidate_id"]])
        return c["candidate_id"], enriched, flags, res.model
    evals = {cid: (enr, flags, model) for cid, enr, flags, model in parallel(evidence, cands, workers)}
    for cid, (enr, flags, model) in sorted(evals.items()):
        print(f"  {cid} {total_score(enr):5.1f} {model} {flags or ''}")

    print(f"3/4 Interview kits and invitation drafts (top {args.kits_top})")

    def kit_and_invite(c):
        cid = c["candidate_id"]
        patient(agents.interview_kit, client, redacted[cid], profile, evals[cid][0])
        patient(agents.invite_email, client, agents.strengths_for_email(evals[cid][0]))
        return cid
    # Highest-scoring candidates first, so the likely interviewees get the most capable models in the chain.
    ranked = sorted(cands, key=lambda c: -total_score(evals[c["candidate_id"]][0]))[:args.kits_top]
    parallel(kit_and_invite, ranked, workers)

    if not args.skip_consistency:
        print("4/4 Consistency check (independent repeat calls, not cached)")
        prompt = load_prompt("evidence_extractor.v1")
        core = agents.criteria_core(profile)
        rows = []
        for cid in CONSISTENCY_SAMPLE:
            user = prompt.user.substitute({"criteria": json.dumps(core, indent=1), "cv": redacted[cid]})
            scores, levels = [], []
            for _ in range(2):
                # Same model that produced the cached result, so this measures repeatability, not model differences.
                out = patient(client._call, evals[cid][2], prompt.system, user, CandidateEvaluation)
                enr, _ = enrich_assessments(out["assessments"], profile["criteria"], redacted[cid])
                scores.append(total_score(enr))
                levels.append([a["level"] for a in enr])
            changed = sum(a != b for a, b in zip(*levels))
            rows.append({"candidate": cid, "model": evals[cid][2], "run 1 score": scores[0], "run 2 score": scores[1],
                         "difference": round(abs(scores[0] - scores[1]), 1),
                         "criteria with a different level": changed,
                         "within 5 points": abs(scores[0] - scores[1]) <= 5})
            print(f"  {rows[-1]}")
        (config.CACHE_DIR / "consistency.json").write_text(json.dumps({
            "model": ", ".join(sorted({evals[c][2] for c in CONSISTENCY_SAMPLE})), "created_at": datetime.now(timezone.utc).isoformat(), "rows": rows,
        }, indent=1), encoding="utf-8")

    print(f"Done in {time.time() - t0:.0f}s. Cache files: {sorted(p.name for p in config.CACHE_DIR.glob('*.json'))}")


if __name__ == "__main__":
    main()
