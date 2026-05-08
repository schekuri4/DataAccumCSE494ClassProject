"""
Dataset Overview Chart — v4 training data composition
"""
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
from collections import Counter

ROOT = "data/processed/v4/aie_instruction_v4_all.jsonl"
OUT  = "data/processed/v4/dataset_overview.png"

rows = [json.loads(l) for l in open(ROOT, encoding="utf-8") if l.strip()]
total = len(rows)

# ── 1. Data Origin ────────────────────────────────────────────────────────────
# Group by v4_bucket into human-readable corpus names
bucket_map = {
    "bedrock_mutated_bug_fix_pair":              "Synthetic Bedrock\n(bug-fix pairs)",
    "bedrock_compile_failure_negative":          "Synthetic Bedrock\n(compile failures)",
    "clean_corpus_reference":                    "Real AIE Corpus\n(clean reference)",
    "compile_validated_original":                "Real Code\n(compile-validated)",
    "compile_validated_replacement":             "Real Code +\nSynthetic Bugs",
    "negative_from_unvalidated_real_debug_issue":"Real Debug\nIssues",
    "curated_seed_clean":                        "Hand-Curated\nSeeds",
    "curated_seed_bug_fix":                      "Hand-Curated\nSeeds",
}
origin_counts: Counter = Counter()
for r in rows:
    b = r.get("metadata", {}).get("v4_bucket", "unknown")
    label = bucket_map.get(b, b)
    origin_counts[label] += 1

# Merge the two hand-curated seeds
hand = origin_counts.pop("Hand-Curated\nSeeds", 0) + origin_counts.pop("Hand-Curated\nSeeds", 0)
if hand:
    origin_counts["Hand-Curated\nSeeds"] = hand

origin_labels  = list(origin_counts.keys())
origin_values  = list(origin_counts.values())

# ── 2. Example Type ───────────────────────────────────────────────────────────
type_remap = {
    "v4_bedrock_buggy_from_compile_validated_correct": "Bug-Fix Pair\n(Bedrock mutated)",
    "v4_clean_corpus_reference":                       "Clean Code\nReference",
    "v4_bedrock_compile_failure_negative":             "Compile Failure\nNegative",
    "synthetic_taxonomy_bug_fix":                      "Synthetic Taxonomy\nBug-Fix",
    "v4_negative_from_unvalidated_real_debug_issue":   "Real Debug\nIssue",
    "bug_fix_pair":                                    "Curated Bug-Fix\nPair",
    "bug_fix_pair_compiler_error":                     "Curated Bug-Fix\nPair",
    "bug_fix_pair_cropped":                            "Curated Bug-Fix\nPair",
    "taxonomy_debug_scenario":                         "Taxonomy Debug\nScenario",
    "taxonomy_multi_file_debug_scenario":              "Taxonomy Debug\nScenario",
    "v4_seed_clean_code":                              "Curated Bug-Fix\nPair",
    "v4_seed_bug_fix":                                 "Curated Bug-Fix\nPair",
    "multi_file_bug_fix_pair":                         "Curated Bug-Fix\nPair",
}
type_counts: Counter = Counter()
for r in rows:
    v = r.get("metadata", {}).get("variant", "unknown")
    type_counts[type_remap.get(v, v)] += 1

# sort by size
type_items = sorted(type_counts.items(), key=lambda x: -x[1])
type_labels = [x[0] for x in type_items]
type_values = [x[1] for x in type_items]

# ── Colours ───────────────────────────────────────────────────────────────────
PALETTE_ORIGIN = ["#4E79A7","#F28E2B","#59A14F","#76B7B2","#E15759","#B07AA1","#FF9DA7"]
PALETTE_TYPE   = ["#4E79A7","#59A14F","#F28E2B","#E15759","#76B7B2","#B07AA1","#FF9DA7","#9C755F"]

# ── Layout ────────────────────────────────────────────────────────────────────
fig = plt.figure(figsize=(16, 8), facecolor="#1a1a2e")
fig.patch.set_facecolor("#1a1a2e")

gs = GridSpec(1, 2, figure=fig,
              left=0.04, right=0.97, top=0.88, bottom=0.10,
              hspace=0.45, wspace=0.35)

ax_origin = fig.add_subplot(gs[0, 0])
ax_type   = fig.add_subplot(gs[0, 1])

TEXT_COLOR = "#e0e0f0"
LABEL_SIZE = 9

def style_ax(ax):
    ax.set_facecolor("#12122a")
    for spine in ax.spines.values():
        spine.set_visible(False)

# ── Donut: Data Origin ────────────────────────────────────────────────────────
style_ax(ax_origin)
wedges, texts, autotexts = ax_origin.pie(
    origin_values,
    labels=None,
    autopct=lambda p: f"{p:.1f}%" if p > 3 else "",
    startangle=90,
    colors=PALETTE_ORIGIN[:len(origin_values)],
    wedgeprops=dict(width=0.55, edgecolor="#1a1a2e", linewidth=2),
    pctdistance=0.78,
    textprops=dict(color=TEXT_COLOR, fontsize=LABEL_SIZE - 1),
)
for at in autotexts:
    at.set_fontsize(8)
    at.set_color("#ffffff")
ax_origin.set_title("Data Origin", color=TEXT_COLOR, fontsize=13, fontweight="bold", pad=14)

legend_patches = [mpatches.Patch(color=PALETTE_ORIGIN[i], label=f"{origin_labels[i]}  ({origin_values[i]:,})")
                  for i in range(len(origin_labels))]
ax_origin.legend(handles=legend_patches, loc="lower center",
                 bbox_to_anchor=(0.5, -0.38), ncol=2,
                 frameon=False, fontsize=8.5,
                 labelcolor=TEXT_COLOR)

# ── Donut: Example Type ───────────────────────────────────────────────────────
style_ax(ax_type)
wedges2, texts2, autotexts2 = ax_type.pie(
    type_values,
    labels=None,
    autopct=lambda p: f"{p:.1f}%" if p > 3 else "",
    startangle=90,
    colors=PALETTE_TYPE[:len(type_values)],
    wedgeprops=dict(width=0.55, edgecolor="#1a1a2e", linewidth=2),
    pctdistance=0.78,
    textprops=dict(color=TEXT_COLOR, fontsize=LABEL_SIZE - 1),
)
for at in autotexts2:
    at.set_fontsize(8)
    at.set_color("#ffffff")
ax_type.set_title("Training Example Type", color=TEXT_COLOR, fontsize=13, fontweight="bold", pad=14)

legend_patches2 = [mpatches.Patch(color=PALETTE_TYPE[i], label=f"{type_labels[i].replace(chr(10),' ')}  ({type_values[i]:,})")
                   for i in range(len(type_labels))]
ax_type.legend(handles=legend_patches2, loc="lower center",
               bbox_to_anchor=(0.5, -0.38), ncol=2,
               frameon=False, fontsize=8.5,
               labelcolor=TEXT_COLOR)

# ── Title ─────────────────────────────────────────────────────────────────────
fig.text(0.5, 0.95,
         f"AIE Instruction Dataset  —  V4 Corpus Overview  ({total:,} examples)",
         ha="center", va="center",
         color=TEXT_COLOR, fontsize=16, fontweight="bold")

fig.text(0.5, 0.915,
         "AMD/Xilinx Versal AIE kernel debugging · fine-tuning dataset",
         ha="center", va="center",
         color="#9090c0", fontsize=10)

plt.savefig(OUT, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
print(f"Saved → {OUT}")
