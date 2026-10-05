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


def make_client(stub: bool) -> LLMClient:
    client = LLMClient("live")
    if stub:
        from tests.stub_llm import STUB_MODEL, stub_generate
        client.mode, client.model = "live", STUB_MODEL
        client._generate = stub_generate
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
            if done % 5 == 0:
                CACHE.save()
                print(f"  {done}/{len(items)}", flush=True)
    CACHE.save()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stub", action="store_true")
    ap.add_argument("--skip-consistency", action="store_true")
    args = ap.parse_args()
    client = make_client(args.stub)
    workers = 1 if args.stub else config.LLM_CONCURRENCY
    t0 = time.time()

    print(f"Model: {client.model} · cache: {config.CACHE_DIR}")
    print("1/4 Success profile")
    profile, res = agents.build_profile(client, jd_text())
    CACHE.save()
    if not args.stub:
        (config.DATA_DIR / "success_profile_v1.json").write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  {len(profile['criteria'])} criteria, weights {[c['weight'] for c in profile['criteria']]} ({res.source})")

    cands = read_candidates_csv()
    redacted = {}
    for c in cands:
        red, _ = redact(c["cv_text"], c)
        if pii_leaks(red, c):
            sys.exit(f"PII leak in {c['candidate_id']}; fix redaction before calling the API")
        redacted[c["candidate_id"]] = red

    print(f"2/4 Evidence extraction for {len(cands)} CVs")

    def evidence(c):
        out, _ = agents.extract_evidence(client, redacted[c["candidate_id"]], profile)
        enriched, flags = enrich_assessments(out["assessments"], profile["criteria"], redacted[c["candidate_id"]])
        return c["candidate_id"], enriched, flags
    evals = {cid: (enr, flags) for cid, enr, flags in parallel(evidence, cands, workers)}
    for cid, (enr, flags) in sorted(evals.items()):
        print(f"  {cid} {total_score(enr):5.1f} {flags or ''}")

    print("3/4 Interview kits and invitation drafts")

    def kit_and_invite(c):
        cid = c["candidate_id"]
        agents.interview_kit(client, redacted[cid], profile, evals[cid][0])
        agents.invite_email(client, agents.strengths_for_email(evals[cid][0]))
        return cid
    parallel(kit_and_invite, cands, workers)

    if not args.skip_consistency:
        print("4/4 Consistency check (independent repeat calls, not cached)")
        prompt = load_prompt("evidence_extractor.v1")
        core = agents.criteria_core(profile)
        rows = []
        for cid in CONSISTENCY_SAMPLE:
            user = prompt.user.substitute({"criteria": json.dumps(core, indent=1), "cv": redacted[cid]})
            scores, levels = [], []
            for _ in range(2):
                out = client._generate(prompt.system, user, CandidateEvaluation)
                enr, _ = enrich_assessments(out["assessments"], profile["criteria"], redacted[cid])
                scores.append(total_score(enr))
                levels.append([a["level"] for a in enr])
            changed = sum(a != b for a, b in zip(*levels))
            rows.append({"candidate": cid, "run 1 score": scores[0], "run 2 score": scores[1],
                         "difference": round(abs(scores[0] - scores[1]), 1),
                         "criteria with a different level": changed,
                         "within 5 points": abs(scores[0] - scores[1]) <= 5})
            print(f"  {rows[-1]}")
        (config.CACHE_DIR / "consistency.json").write_text(json.dumps({
            "model": client.model, "created_at": datetime.now(timezone.utc).isoformat(), "rows": rows,
        }, indent=1), encoding="utf-8")

    print(f"Done in {time.time() - t0:.0f}s. Cache files: {sorted(p.name for p in config.CACHE_DIR.glob('*.json'))}")


if __name__ == "__main__":
    main()
