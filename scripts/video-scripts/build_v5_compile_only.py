"""
Build V5 dataset: V4 filtered to only rows with real compile errors.
Drops the 5,107 semantic-bug rows (wsl_compile_ok=True) that compiled clean.
Keeps the 3,095 rows where xchesscc actually failed (wsl_compile_ok missing).
"""
import json
import random
from pathlib import Path

SEED = 42
VAL_RATIO = 0.135  # same as V4 (~1/7.4)

v4_path = Path("data/processed/v4/aie_instruction_v4_all.jsonl")
out_dir  = Path("data/processed/v5")
out_dir.mkdir(parents=True, exist_ok=True)

rows = [json.loads(l) for l in v4_path.read_text(encoding="utf-8").splitlines() if l.strip()]

kept = [r for r in rows if r.get("metadata", {}).get("wsl_compile_ok") is not True]
dropped = len(rows) - len(kept)
print(f"V4 total: {len(rows)}  |  kept (compile errors): {len(kept)}  |  dropped (semantic): {dropped}")

# Shuffle and split
rng = random.Random(SEED)
rng.shuffle(kept)
n_val = round(len(kept) * VAL_RATIO)
val_rows   = kept[:n_val]
train_rows = kept[n_val:]
print(f"Train: {len(train_rows)}  |  Val: {len(val_rows)}")

def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

write_jsonl(out_dir / "aie_instruction_v5_all.jsonl",        kept)
write_jsonl(out_dir / "aie_instruction_v5_train.jsonl",      train_rows)
write_jsonl(out_dir / "aie_instruction_v5_validation.jsonl", val_rows)

print(f"Written to {out_dir}/")
