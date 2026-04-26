#!/usr/bin/env python3
import json
from pathlib import Path

old_correct = Path("data/processed/v3/bedrock_compile_validated_correct_full_budget80.jsonl")
new_correct = Path("data/processed/v3/bedrock_expanded_topics_correct_compile_validated.jsonl")
mutations   = Path("data/processed/v3/bedrock_buggy_from_compile_validated_correct.jsonl")
flagged     = Path("data/processed/v3/correct_baselines_offtopic.jsonl")

def count_ok(p):
    return sum(1 for line in p.open(encoding="utf-8") if line.strip() and json.loads(line).get("compile_ok") is True)

old_ok = count_ok(old_correct) if old_correct.exists() else 0
new_ok = count_ok(new_correct) if new_correct.exists() else 0

mut_rows = [json.loads(l) for l in mutations.open(encoding="utf-8") if l.strip()]
accepted  = [r for r in mut_rows if r.get("parse_ok") and r.get("buggy") and r.get("correct")]
rejected  = [r for r in mut_rows if not (r.get("parse_ok") and r.get("buggy") and r.get("correct"))]

flagged_rows = [json.loads(l) for l in flagged.open(encoding="utf-8") if l.strip()]
flagged_ids  = {str(r.get("source_id") or "") for r in flagged_rows if r.get("source_id")}

removed_from_v4   = sum(1 for r in accepted if str(r.get("source_id") or "") in flagged_ids)
kept_in_v4        = len(accepted) - removed_from_v4

print("=== Mutation pair accounting ===")
print("")
print("CORRECT BASELINES:")
print("  Old pipeline (full_budget80):   %5d compile-ok rows" % old_ok)
print("  New expanded topics:            %5d compile-ok rows" % new_ok)
print("  Total fed into mutation gen:    %5d rows" % (old_ok + new_ok))
print("")
print("MUTATION GENERATION (combined output file):")
print("  Total attempts:                 %5d" % len(mut_rows))
print("  Accepted pairs:                 %5d" % len(accepted))
print("  Rejected pairs:                 %5d  (buggy_same_as_correct / layout_changed / quality_gate)" % len(rejected))
print("  Acceptance rate:                %5.0f%%" % (100 * len(accepted) / max(len(mut_rows), 1)))
print("")
print("AFTER OFF-TOPIC CLEANUP:")
print("  Accepted rows from flagged baselines: %5d  <- removed from V4" % removed_from_v4)
print("  Remaining on-topic mutation pairs:    %5d  <- in V4 now" % kept_in_v4)
print("  Flagged off-topic correct baselines:  %5d  (of %d new)" % (len(flagged_rows), new_ok))
print("")
print("BOTTOM LINE:")
print("  3,010 new baselines * ~71%% mutation accept rate = ~2,229 new mutations")
print("  2,229 new + 2,381 old = 4,610 total accepted")
print("  4,610 - 819 off-topic = 3,791 in V4")
