#!/usr/bin/env python3
"""run_pipeline.py — Full AIE dataset generation pipeline orchestrator.

Pipeline stages (in order):
  1. generate   — Bedrock: generate correct AIE mini-projects per bug slug/topic
  2. compile    — WSL: compile-validate the correct baselines
  3. mutate     — Bedrock: introduce bugs into compile-ok correct code
  4. negatives  — Bedrock: fix compile failures → compile-failure negatives
  5. merge_neg  — Add compile-failure negatives to V4
  6. merge_mut  — Add mutation pairs to V4
  7. screen     — Keyword-relevance screen of correct baselines
  8. rm_offtop  — Remove V4 mutation rows derived from off-topic baselines
  9. audit      — Bedrock: verify each mutation pair actually has the bug
  10. rm_absent — Remove bug-absent pairs from V4 + write retry queue
  11. retry     — Bedrock: re-mutate the bug-absent source_ids
  12. add_retry — Add re-mutated pairs to V4

Use --stages to run a subset:
    python scripts/run_pipeline.py --stages generate compile mutate

Each stage is skipped if its output already appears to be up-to-date
(use --force to bypass the check).

Environment:
    AWS_BEARER_TOKEN_BEDROCK   Required for all Bedrock stages
    WORKERS                    Override worker count (default: 12)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V3  = ROOT / "data" / "processed" / "v3"
V4  = ROOT / "data" / "processed" / "v4"
PY  = Path(sys.executable)  # use same venv Python

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(cmd: list[str | Path], *, check: bool = True) -> subprocess.CompletedProcess:
    print(f"\n{'='*70}")
    print(f"CMD: {' '.join(str(c) for c in cmd)}")
    print(f"{'='*70}\n")
    result = subprocess.run([str(c) for c in cmd], check=check)
    return result


def jsonl_count(path: Path, filter_fn=None) -> int:
    """Count rows in a JSONL file, optionally filtered."""
    if not path.exists():
        return 0
    n = 0
    for line in path.open(encoding="utf-8"):
        if not line.strip():
            continue
        if filter_fn is None:
            n += 1
        else:
            try:
                if filter_fn(json.loads(line)):
                    n += 1
            except Exception:
                pass
    return n


def require_token():
    if not os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        print("ERROR: AWS_BEARER_TOKEN_BEDROCK is not set.", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------

STAGE_ORDER = [
    "generate",
    "compile",
    "mutate",
    "negatives",
    "merge_neg",
    "merge_mut",
    "screen",
    "rm_offtop",
    "audit",
    "rm_absent",
    "retry",
    "add_retry",
]

STAGE_DESCRIPTIONS = {
    "generate":  "Bedrock: generate correct AIE code for each bug-topic slug",
    "compile":   "WSL Vitis: compile-validate the generated correct baselines",
    "mutate":    "Bedrock: introduce bugs into compile-ok correct code",
    "negatives": "Bedrock: fix compile failures and extract compile-failure negatives",
    "merge_neg": "Merge compile-failure negatives into V4",
    "merge_mut": "Merge mutation bug-fix pairs into V4",
    "screen":    "Keyword-relevance screen: flag off-topic correct baselines",
    "rm_offtop": "Remove V4 mutation rows derived from off-topic baselines",
    "audit":     "Bedrock: verify bug is actually present in each mutation pair",
    "rm_absent": "Remove bug-absent pairs from V4 + write retry queue",
    "retry":     "Bedrock: re-mutate the bug-absent source_ids",
    "add_retry": "Merge re-mutated pairs into V4",
}


def stage_generate(args):
    require_token()
    correct_file = V3 / "bedrock_expanded_topics_correct_compile_validated.jsonl"
    already = jsonl_count(correct_file)
    if already > 0 and not args.force:
        print(f"[SKIP] generate — {correct_file.name} has {already} rows already. Use --force to re-run.")
        return
    run([PY, ROOT / "scripts" / "bedrock_synth_taxonomy.py",
         "--output", correct_file,
         "--workers", str(args.workers),
         "--scope", "correct",
         "--target", "AIE"])


def stage_compile(args):
    correct_file = V3 / "bedrock_expanded_topics_correct_compile_validated.jsonl"
    ok_count = jsonl_count(correct_file, lambda r: r.get("compile_ok") is True)
    if ok_count > 0 and not args.force:
        print(f"[SKIP] compile — {ok_count} compile-ok rows already in {correct_file.name}. Use --force to re-run.")
        return
    run(["wsl", "-d", "Ubuntu-24.04", "--",
         "bash", str(ROOT / "scripts" / "run_validate_wsl.sh"),
         "--scope", "correct",
         "--target", "AIE",
         "--workers", str(args.workers),
         "--timeout", "60"])


def stage_mutate(args):
    require_token()
    mutation_file = V3 / "bedrock_buggy_from_compile_validated_correct.jsonl"
    accepted = jsonl_count(mutation_file,
                           lambda r: r.get("parse_ok") and r.get("buggy") and r.get("correct"))
    if accepted > 0 and not args.force:
        print(f"[SKIP] mutate — {accepted} accepted mutation pairs already exist. Use --force to re-run.")
        return
    run([PY, ROOT / "scripts" / "generate_bedrock_buggy_from_correct.py",
         "--input",   V3 / "bedrock_expanded_topics_correct_compile_validated.jsonl",
         "--output",  mutation_file,
         "--workers", str(args.workers),
         "--resume"])


def stage_negatives(args):
    """Use Bedrock to fix compile failures → extract compile-failure negatives."""
    require_token()
    out_synth = V3 / "bedrock_fixed_synth.jsonl"
    out_real  = V3 / "bedrock_fixed_real.jsonl"
    # Find compile-failure rows to use as input
    correct_file = V3 / "bedrock_expanded_topics_correct_compile_validated.jsonl"
    if not correct_file.exists():
        print("[SKIP] negatives — no compile results file found. Run 'compile' stage first.")
        return
    if (out_synth.exists() or out_real.exists()) and not args.force:
        print(f"[SKIP] negatives — output files already exist. Use --force to re-run.")
        return
    run([PY, ROOT / "scripts" / "bedrock_fix_compile_failures.py",
         "--results",   correct_file,
         "--dataset",   correct_file,
         "--out-synth", out_synth,
         "--out-real",  out_real,
         "--workers",   str(args.workers),
         "--resume"])


def stage_merge_neg(args):
    out_file = V4 / "aie_instruction_v4_all.jsonl"
    run([PY, ROOT / "scripts" / "add_bedrock_compile_negatives_to_v4.py",
         "--v4-dir", V4])


def stage_merge_mut(args):
    mutation_file = V3 / "bedrock_buggy_from_compile_validated_correct.jsonl"
    run([PY, ROOT / "scripts" / "add_bedrock_mutations_to_v4.py",
         "--mutations", mutation_file,
         "--v4-dir",    V4])


def stage_screen(args):
    correct_file = V3 / "bedrock_expanded_topics_correct_compile_validated.jsonl"
    kept    = V3 / "correct_baselines_kept.jsonl"
    flagged = V3 / "correct_baselines_offtopic.jsonl"
    if kept.exists() and flagged.exists() and not args.force:
        kn = jsonl_count(kept)
        fn = jsonl_count(flagged)
        print(f"[SKIP] screen — kept={kn} flagged={fn} already. Use --force to re-run.")
        return
    run([PY, ROOT / "scripts" / "screen_offtopic_correct_baselines.py",
         "--input",   correct_file,
         "--kept",    kept,
         "--flagged", flagged])


def stage_rm_offtop(args):
    run([PY, ROOT / "scripts" / "remove_offtopic_mutations_from_v4.py",
         "--flagged", V3 / "correct_baselines_offtopic.jsonl",
         "--v4-dir",  V4])


def stage_audit(args):
    require_token()
    audit_file = V4 / "bug_presence_audit.jsonl"
    v4_file    = V4 / "aie_instruction_v4_all.jsonl"
    total_pairs = jsonl_count(v4_file,
        lambda r: (r.get("metadata") or {}).get("v4_bucket") == "bedrock_mutated_bug_fix_pair")
    already_audited = jsonl_count(audit_file, lambda r: "bug_present" in r)
    if already_audited >= total_pairs and total_pairs > 0 and not args.force:
        print(f"[SKIP] audit — all {total_pairs} pairs already audited. Use --force to re-run.")
        return
    run([PY, ROOT / "scripts" / "audit_bug_presence_v4.py",
         "--input",   v4_file,
         "--output",  audit_file,
         "--workers", str(args.workers)])


def stage_rm_absent(args):
    run([PY, ROOT / "scripts" / "remove_bugabsent_from_v4.py",
         "--audit",  V4 / "bug_presence_audit.jsonl",
         "--v4-dir", V4])


def stage_retry(args):
    require_token()
    retry_queue = V4 / "bug_absent_source_ids.jsonl"
    retry_out   = V3 / "bedrock_buggy_remutated.jsonl"
    if not retry_queue.exists():
        print("[SKIP] retry — no retry queue found. Run 'rm_absent' stage first.")
        return
    queued = jsonl_count(retry_queue)
    done   = jsonl_count(retry_out,
                         lambda r: r.get("parse_ok") and r.get("buggy") and r.get("correct"))
    if done >= queued and queued > 0 and not args.force:
        print(f"[SKIP] retry — all {queued} entries already re-mutated. Use --force to re-run.")
        return
    run([PY, ROOT / "scripts" / "remutate_bugabsent.py",
         "--retry-queue", retry_queue,
         "--output",      retry_out,
         "--workers",     str(args.workers),
         "--resume"])


def stage_add_retry(args):
    retry_out = V3 / "bedrock_buggy_remutated.jsonl"
    if not retry_out.exists():
        print("[SKIP] add_retry — no remutated file found. Run 'retry' stage first.")
        return
    run([PY, ROOT / "scripts" / "add_bedrock_mutations_to_v4.py",
         "--mutations", retry_out,
         "--v4-dir",    V4])


STAGE_FNS = {
    "generate":  stage_generate,
    "compile":   stage_compile,
    "mutate":    stage_mutate,
    "negatives": stage_negatives,
    "merge_neg": stage_merge_neg,
    "merge_mut": stage_merge_mut,
    "screen":    stage_screen,
    "rm_offtop": stage_rm_offtop,
    "audit":     stage_audit,
    "rm_absent": stage_rm_absent,
    "retry":     stage_retry,
    "add_retry": stage_add_retry,
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--stages", nargs="+", metavar="STAGE",
        help=f"Stages to run (default: all). Choices: {', '.join(STAGE_ORDER)}",
    )
    parser.add_argument(
        "--from-stage", metavar="STAGE",
        help="Start from this stage and run all subsequent stages.",
    )
    parser.add_argument(
        "--workers", type=int, default=int(os.environ.get("WORKERS", "12")),
        help="Worker count for parallel stages (default: 12)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Force re-run even if outputs already exist",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List all stages and exit",
    )
    args = parser.parse_args()

    if args.list:
        print("Pipeline stages (in order):")
        for i, name in enumerate(STAGE_ORDER, 1):
            print(f"  {i:2d}. {name:<12} — {STAGE_DESCRIPTIONS[name]}")
        return

    # Determine which stages to run
    if args.from_stage:
        if args.from_stage not in STAGE_ORDER:
            print(f"Unknown stage: {args.from_stage}. Valid: {STAGE_ORDER}", file=sys.stderr)
            sys.exit(1)
        idx = STAGE_ORDER.index(args.from_stage)
        stages_to_run = STAGE_ORDER[idx:]
    elif args.stages:
        invalid = [s for s in args.stages if s not in STAGE_ORDER]
        if invalid:
            print(f"Unknown stages: {invalid}. Valid: {STAGE_ORDER}", file=sys.stderr)
            sys.exit(1)
        stages_to_run = [s for s in STAGE_ORDER if s in args.stages]
    else:
        stages_to_run = STAGE_ORDER

    print(f"Running {len(stages_to_run)} stage(s): {', '.join(stages_to_run)}")
    print(f"Workers: {args.workers}  Force: {args.force}\n")

    t_total = time.time()
    results: dict[str, str] = {}

    for stage_name in stages_to_run:
        t0 = time.time()
        print(f"\n{'#'*70}")
        print(f"# STAGE: {stage_name.upper()}")
        print(f"# {STAGE_DESCRIPTIONS[stage_name]}")
        print(f"{'#'*70}")
        try:
            STAGE_FNS[stage_name](args)
            elapsed = time.time() - t0
            results[stage_name] = f"OK ({elapsed:.0f}s)"
        except subprocess.CalledProcessError as exc:
            elapsed = time.time() - t0
            results[stage_name] = f"FAILED (exit {exc.returncode})"
            print(f"\nStage '{stage_name}' FAILED with exit code {exc.returncode}", file=sys.stderr)
            print("Stopping pipeline.", file=sys.stderr)
            break
        except Exception as exc:
            results[stage_name] = f"ERROR: {exc}"
            print(f"\nStage '{stage_name}' raised an exception: {exc}", file=sys.stderr)
            import traceback; traceback.print_exc()
            break

    total_elapsed = time.time() - t_total
    print(f"\n{'='*70}")
    print(f"PIPELINE SUMMARY  ({total_elapsed:.0f}s total)")
    print(f"{'='*70}")
    for stage_name in stages_to_run:
        status = results.get(stage_name, "NOT RUN")
        print(f"  {stage_name:<12}  {status}")
    print()


if __name__ == "__main__":
    main()
