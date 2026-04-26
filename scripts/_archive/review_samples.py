"""Quick quality review: sample compile_ok rows and print correct code + bug label."""
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "data" / "processed" / "v3" / "bedrock_compile_validated_correct_full_budget80.jsonl"

rows = []
with path.open(encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("compile_ok") is True:
            rows.append(row)

random.seed(42)
samples = random.sample(rows, 10)
for i, row in enumerate(samples, 1):
    print("=" * 72)
    print(f"[{i}/10]  SLUG : {row['slug']}")
    print(f"        LABEL: {row['label']}")
    print(f"        TIER : {row['tier']}")
    print()
    print(row["correct"])
    print()
