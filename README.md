# DataAccum CSE494 Class Project

Supervised fine-tuning (SFT) dataset for AMD/Xilinx Versal AI Engine (AIE) code
understanding, debugging, and graph analysis. The V4 pipeline uses AWS Bedrock
(DeepSeek V3) to synthetically generate correct AIE mini-projects, compile-validate
them with real Vitis 2025.2 tooling, then introduce verified bugs.

## Dataset Summary (V4 — current)

| Metric | Value |
|---|---|
| Total rows | **12,920** |
| Training rows | 11,022 |
| Validation rows | 1,898 |
| Verified bug-fix pairs | **3,246** (all audit-confirmed) |
| Compile-validated negatives | 2,311 |
| Clean corpus references | 2,879 |

### V4 Bucket Breakdown

| Bucket | Rows |
|---|---|
| `bedrock_mutated_bug_fix_pair` | 3,246 |
| `clean_corpus_reference` | 2,879 |
| `bedrock_compile_failure_negative` | 2,311 |
| `negative_from_unvalidated_real_debug_issue` | 1,655 |
| `compile_validated_replacement` | 1,521 |
| `compile_validated_original` | 1,295 |
| `curated_seed_clean` | 11 |
| `curated_seed_bug_fix` | 2 |

## Pipeline Architecture

The V4 dataset is produced by a 12-stage pipeline orchestrated by `scripts/run_pipeline.py`.

```
generate → compile → mutate → negatives → merge_neg → merge_mut
    → screen → rm_offtop → audit → rm_absent → retry → add_retry
```

### Stage Details

| Stage | Script | Description |
|---|---|---|
| `generate` | `bedrock_synth_taxonomy.py` | Bedrock: generate correct AIE mini-projects per bug-topic slug |
| `compile` | `run_validate_wsl.sh` | WSL Vitis 2025.2: compile-validate the generated baselines |
| `mutate` | `generate_bedrock_buggy_from_correct.py` | Bedrock: introduce targeted bugs into compile-ok correct code |
| `negatives` | `bedrock_fix_compile_failures.py` | Bedrock: fix compile failures → extract compile-failure negatives |
| `merge_neg` | `add_bedrock_compile_negatives_to_v4.py` | Add compile-failure negatives to V4 dataset |
| `merge_mut` | `add_bedrock_mutations_to_v4.py` | Add mutation bug-fix pairs to V4 dataset |
| `screen` | `screen_offtopic_correct_baselines.py` | Keyword-relevance screen: flag off-topic baselines |
| `rm_offtop` | `remove_offtopic_mutations_from_v4.py` | Remove V4 rows derived from off-topic baselines |
| `audit` | `audit_bug_presence_v4.py` | Bedrock: verify each mutation pair actually has the claimed bug |
| `rm_absent` | `remove_bugabsent_from_v4.py` | Remove bug-absent pairs + write retry queue |
| `retry` | `remutate_bugabsent.py` | Bedrock: re-mutate the failed source_ids with stronger prompting |
| `add_retry` | `add_bedrock_mutations_to_v4.py` | Merge re-mutated pairs into V4 |

### Running the Pipeline

```powershell
# Prerequisites
$env:AWS_BEARER_TOKEN_BEDROCK = "<your-bedrock-token>"

# Full pipeline
python scripts/run_pipeline.py

# Resume from a specific stage
python scripts/run_pipeline.py --from-stage audit

# Run specific stages only
python scripts/run_pipeline.py --stages mutate merge_mut audit rm_absent retry add_retry

# Force re-run even if outputs already exist
python scripts/run_pipeline.py --stages audit --force --workers 12

# List all stages
python scripts/run_pipeline.py --list
```

Each stage has smart idempotency: if its output already exists it is skipped
unless `--force` is passed.

## AIE Mini-Project Format

Each synthetic project follows a two-file pattern that the real Vitis compiler
can actually process:

```
// FILE: graph.h        ← uses <adf.h>, declares ports and graph topology
// FILE: kernels/<name>.cc   ← uses readincr/writeincr for stream I/O
```

The compiler is invoked as `aiecompiler --target=hw` inside WSL Ubuntu-24.04
with Vitis 2025.2 mounted at `/vitis/2025.2/Vitis`.

## Bug Taxonomy

The dataset covers **205 bug-topic slugs** across 8 broad categories:

- **Stream I/O**: token imbalance/deadlock, cascade protocol errors, wrong port direction
- **Memory**: buffer overflows, window OOB, unaligned loads, wrong tile assignment
- **Pipelining**: missing `chess_prepare_for_pipelining`, wrong `runtime<ratio>`, loop breaks
- **Data types**: `acc48`-for-`acc80`, `bfloat16` on AIE1, signed/unsigned mismatch, wrong vector lane width
- **Graph topology**: reversed `connect`, self-loop, missing `adf::source`, duplicate reads
- **Compile budget**: program memory exceeded, tile overcommit
- **Math/logic**: subtraction-for-addition, modulo in loop, accumulator reset bugs
- **PLIO/GMIO**: PLIO width mismatch, `GMIO` burst length errors

## Bug Verification (Audit System)

All mutation pairs are audited by asking Bedrock to confirm the bug is actually
present before inclusion in V4. Audit result from first full run:

| Result | Count | % |
|---|---|---|
| Bug present ✅ | 3,246 | 85.6% |
| Bug absent ❌ (removed) | 545 | 14.4% |
| **Total audited** | **3,791** | 100% |

The 545 removed pairs are written to `data/processed/v4/bug_absent_source_ids.jsonl`
and re-attempted by the `retry` stage using a stronger prompt that includes the
audit failure explanation and higher sampling temperature.

## Repository Structure

```
scripts/
  run_pipeline.py                        ← main orchestrator (run this)
  bedrock_synth_taxonomy.py              ← stage: generate
  run_validate_wsl.sh                    ← stage: compile (WSL Vitis)
  generate_bedrock_buggy_from_correct.py ← stage: mutate
  bedrock_fix_compile_failures.py        ← stage: negatives
  add_bedrock_compile_negatives_to_v4.py ← stage: merge_neg
  add_bedrock_mutations_to_v4.py         ← stage: merge_mut / add_retry
  screen_offtopic_correct_baselines.py   ← stage: screen
  remove_offtopic_mutations_from_v4.py   ← stage: rm_offtop
  audit_bug_presence_v4.py               ← stage: audit
  remove_bugabsent_from_v4.py            ← stage: rm_absent
  remutate_bugabsent.py                  ← stage: retry
  build_verified_v4_dataset.py           ← builds final V4 train/val splits
  scripts/_archive/                      ← superseded V1–V3 scripts

data/
  raw/
    bedrock_expanded_bug_topics.txt      ← 205-slug bug taxonomy with prompts
  processed/
    v4/
      aie_instruction_v4_summary.json   ← dataset stats (committed)
      bug_presence_audit.jsonl           ← per-pair audit results (committed)
      bug_absent_source_ids.jsonl        ← retry queue for failed mutations
      aie_instruction_v4_all.jsonl       ← full dataset (gitignored, >50 MB)
      aie_instruction_v4_train.jsonl     ← training split (gitignored)
      aie_instruction_v4_validation.jsonl← validation split (gitignored)
    v3/                                  ← intermediate files (gitignored)

axolotl/                                 ← fine-tuning configs (QLoRA, SFT)
unsloth/                                 ← Windows fine-tuning guide
aie_dataset/                             ← curated real AIE source examples
```

## Environment Setup

### Python (Windows)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install boto3 requests tqdm
```

### WSL Vitis (compile validation)

Vitis 2025.2 must be installed at `/vitis/2025.2/Vitis` inside Ubuntu-24.04 on WSL.
The compile validator is invoked automatically by the pipeline via `run_validate_wsl.sh`.

### AWS Bedrock

Set the bearer token before running any Bedrock stage:

```powershell
$env:AWS_BEARER_TOKEN_BEDROCK = "<your-token>"
```

Model used: `deepseek.v3.2`, region `us-east-1`.

## Fine-Tuning

See `axolotl/` for QLoRA configs targeting Qwen2.5-Coder-7B and 14B, and
`unsloth/LOCAL_WINDOWS_FINE_TUNE.md` for local Windows fine-tuning with Unsloth.

## Citations

See `CITATIONS.md` for all referenced works and dataset sources.
