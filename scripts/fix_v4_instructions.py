#!/usr/bin/env python3
"""Fix non-fix-task rows in V4 to use fix-task format.

Three row types get converted:
1. known-good reference rows  -> replace instruction with a fix-task instruction
                                  and set context to the correct code,
                                  response to the same correct code (identity fix).
                                  These rows have no buggy code, so they become
                                  "correct code passthrough" examples — drop them
                                  entirely since they have no bug signal.

2. compile-ready inspection   -> the context IS buggy code (it has a // case: comment).
                                  Response was "No - not compile-ready."
                                  We drop these rows: the correct answer is unknown
                                  (we don't have the fixed version).

3. yes/no detection rows      -> the context is CORRECT (non-buggy) code being asked
                                  "does this have bug X?" and the answer was always "No."
                                  These are adversarial negative examples — useful as
                                  fix tasks where the correct response is returning
                                  the same code unchanged, BUT we don't have a
                                  paired buggy version. Drop these too.

NET EFFECT: Only keep clean fix-task rows. This removes 6,845 noise rows,
leaving 8,584 high-signal fix-task rows.

Actually wait — let me reconsider. The yes/no detection rows DO have the correct code
in context, and "No" as the answer. We can keep these as-is, OR convert them to
"review this code for bugs, return corrected version" where response == context
(passthrough — no bug). This gives the model examples of correct code to return
unchanged. Let's do that.

For compile-ready rows: context has BUGGY code with // case: comments but we have
no fixed version. Best to drop these.

For known-good rows: context is just "Known-good AIE reference path: ...", response
is the correct code. We can convert to passthrough fix tasks (no bug present).

Summary of actions:
- known-good (2879): Convert to fix-task passthrough — instruction = fix phrasing,
  context = the response code (strip fence), response = same code (strip fence)
- compile-ready (2311): DROP — buggy code, no fix available
- yes/no detection (1655): Convert to fix-task passthrough — instruction = fix phrasing,
  context = the code in context, response = same code (no change needed)
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V4_ALL = ROOT / "data" / "processed" / "v4" / "aie_instruction_v4_all.jsonl"
V4_TRAIN = ROOT / "data" / "processed" / "v4" / "aie_instruction_v4_train.jsonl"
V4_VAL = ROOT / "data" / "processed" / "v4" / "aie_instruction_v4_validation.jsonl"

# Passthrough fix instructions — varied phrasings for "review and return"
PASSTHROUGH_INSTRS = [
    "Review this AIE source for bugs. Return the complete corrected file.",
    "Inspect this AIE code and fix any bugs. Return the full file.",
    "Check this AIE source for defects. Return the corrected version.",
    "Find and fix any bugs in this AIE code. Return the complete file.",
    "Debug this AIE source. Return the full corrected file.",
    "Look for bugs in this AIE code. Return the complete fixed source.",
    "Examine this AIE kernel/graph for errors. Return the corrected file.",
    "Review this Versal AIE code. Fix any defects and return the full file.",
]

# Passthrough responses are the code itself unchanged — we need a consistent response
# format. Existing fix rows return fenced code blocks, so match that.
PASSTHROUGH_RESP_TEMPLATE = "```cpp\n{code}\n```"


def strip_fence(text: str) -> str:
    text = text.strip()
    for lang in ("```cpp", "```c++", "```c", "```"):
        if text.startswith(lang):
            text = text[len(lang):].strip()
            break
    if text.endswith("```"):
        text = text[:-3].strip()
    return text


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def is_known_good(row: dict) -> bool:
    return "known-good" in row.get("instruction", "")


def is_compile_ready(row: dict) -> bool:
    return "compile-ready" in row.get("instruction", "")


def is_yes_no_detection(row: dict) -> bool:
    return row.get("instruction", "").startswith("Does this AIE")


def convert_known_good(row: dict, rng: random.Random) -> dict | None:
    """Convert known-good row to a passthrough fix task."""
    code = strip_fence(row.get("response", ""))
    if not code or len(code) < 50:
        return None  # skip empty/tiny
    instr = rng.choice(PASSTHROUGH_INSTRS)
    new_row = dict(row)
    new_row["instruction"] = instr
    new_row["context"] = code
    new_row["response"] = PASSTHROUGH_RESP_TEMPLATE.format(code=code)
    meta = dict(row.get("metadata", {}))
    meta["original_instruction_type"] = "known_good_reference"
    meta["instruction_converted"] = True
    new_row["metadata"] = meta
    return new_row


def convert_yes_no(row: dict, rng: random.Random) -> dict | None:
    """Convert yes/no detection row to a passthrough fix task."""
    code = row.get("context", "").strip()
    if not code or len(code) < 50:
        return None
    instr = rng.choice(PASSTHROUGH_INSTRS)
    new_row = dict(row)
    new_row["instruction"] = instr
    new_row["context"] = code
    new_row["response"] = PASSTHROUGH_RESP_TEMPLATE.format(code=code)
    meta = dict(row.get("metadata", {}))
    meta["original_instruction_type"] = "yes_no_detection"
    meta["instruction_converted"] = True
    new_row["metadata"] = meta
    return new_row


def main() -> None:
    rng = random.Random(42)

    print("Loading V4 all rows...")
    all_rows = load_jsonl(V4_ALL)
    print(f"  Loaded {len(all_rows)} rows")

    # Backup
    backup = V4_ALL.with_suffix(".jsonl.preinstrfix")
    if not backup.exists():
        print(f"  Backing up to {backup.name}")
        write_jsonl(backup, all_rows)
    else:
        print(f"  Backup already exists: {backup.name}")

    known_good_rows = [r for r in all_rows if is_known_good(r)]
    compile_rows = [r for r in all_rows if is_compile_ready(r)]
    yes_no_rows = [r for r in all_rows if is_yes_no_detection(r)]
    fix_rows = [r for r in all_rows
                if not is_known_good(r) and not is_compile_ready(r) and not is_yes_no_detection(r)]

    print(f"\nRow type breakdown:")
    print(f"  Fix-task (keep as-is):        {len(fix_rows):5d}")
    print(f"  Known-good (convert):          {len(known_good_rows):5d}")
    print(f"  Compile-ready (drop):          {len(compile_rows):5d}")
    print(f"  Yes/no detection (convert):    {len(yes_no_rows):5d}")

    # Convert known-good rows
    converted_known_good = []
    skipped_kg = 0
    for r in known_good_rows:
        converted = convert_known_good(r, rng)
        if converted:
            converted_known_good.append(converted)
        else:
            skipped_kg += 1
    print(f"\nKnown-good: {len(converted_known_good)} converted, {skipped_kg} skipped (empty/tiny)")

    # Convert yes/no rows
    converted_yes_no = []
    skipped_yn = 0
    for r in yes_no_rows:
        converted = convert_yes_no(r, rng)
        if converted:
            converted_yes_no.append(converted)
        else:
            skipped_yn += 1
    print(f"Yes/no: {len(converted_yes_no)} converted, {skipped_yn} skipped")

    print(f"Compile-ready: {len(compile_rows)} dropped")

    # Reassemble
    new_all = fix_rows + converted_known_good + converted_yes_no
    print(f"\nNew total rows: {len(new_all)} (was {len(all_rows)}, removed {len(all_rows) - len(new_all)})")

    # Verify instruction distribution
    from collections import Counter
    instrs = Counter(r["instruction"] for r in new_all)
    print(f"\nTop 10 instructions in new dataset:")
    for instr, cnt in instrs.most_common(10):
        print(f"  {cnt:5d}  {repr(instr[:100])}")

    # Write new all file
    print(f"\nWriting {V4_ALL.name}...")
    write_jsonl(V4_ALL, new_all)

    # Rebuild train/val splits preserving split assignments from metadata
    train_rows = [r for r in new_all if r.get("metadata", {}).get("split") == "train"]
    val_rows = [r for r in new_all if r.get("metadata", {}).get("split") == "validation"]
    unassigned = [r for r in new_all if r.get("metadata", {}).get("split") not in ("train", "validation")]

    if unassigned:
        print(f"  WARNING: {len(unassigned)} rows have no split assignment")

    print(f"Writing train ({len(train_rows)} rows) and val ({len(val_rows)} rows)...")
    if V4_TRAIN.exists():
        write_jsonl(V4_TRAIN, train_rows)
    if V4_VAL.exists():
        write_jsonl(V4_VAL, val_rows)

    print("\nDone.")


if __name__ == "__main__":
    main()
