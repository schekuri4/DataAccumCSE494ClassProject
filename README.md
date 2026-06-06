# DataAccum AIE Debug Dataset

This project builds compile-grounded instruction datasets for AMD/Xilinx Versal AI Engine (AIE) debugging. The current active artifact is the **v9** dataset, generated from compile-clean golden corpus projects and mutated with AIE-specific bug mutators.

Each training row asks a model to repair buggy AIE/ADF source code from real compiler diagnostics and return a focused unified diff.

## Current Dataset

The active dataset lives in:

```text
data/processed/v9_dataset_40variants/
```

| File | Purpose |
| --- | --- |
| `aie_instruction_v9_all.jsonl` | Full v9 instruction dataset |
| `aie_instruction_v9_train.jsonl` | Training split |
| `aie_instruction_v9_validation.jsonl` | Validation split |
| `manifest_summary_v9.json` | Row counts, bug taxonomy, shard provenance |

Current v9 headline stats:

| Metric | Value |
| --- | ---: |
| Total rows | 6,645 |
| Train rows | 5,869 |
| Validation rows | 776 |
| Compile-clean corpus groups used | 197 |
| Unique bug types | 152 |
| Unique bug categories | 28 |
| Bug count per variant | Random 1-4 |
| Max variants per corpus project | 40 |

Each JSONL row has this shape:

```json
{
  "instruction": "A Versal AIE build is failing...",
  "context": "Buggy project files plus compiler diagnostics...",
  "response": "A unified diff that repairs the project...",
  "metadata": {
    "dataset_version": "v9",
    "split": "train",
    "bug_count": 3,
    "bug_types": ["..."],
    "compile_error_class": "compile_error",
    "target": "AIE"
  }
}
```

## Clean Project Layout

```text
.
├── README.md                         Project overview and workflow
├── CITATIONS.md                      Source/provenance notes
├── archive/                          Local legacy archive notes and ignored archive/local
├── docs/                             Handoff and longer project notes
├── scripts/                          Dataset, corpus, validation, and repair tools
├── bug famalies/                     Bedrock bug-family generation and generated mutators
├── golden repos/                     Hydrated local golden corpus slices
├── data/
│   ├── raw/                          Raw examples and notes
│   └── processed/
│       ├── aie_debug_benchmark_holdout.json
│       ├── v8/                       Previous dataset generation
│       └── v9_dataset_40variants/    Current dataset
└── outputs/
    └── v9_corpus_build/              Local run artifacts, logs, audits, shards, backups
```

`outputs/`, `golden repos/`, large JSONL files, Vitis build products, local caches, and secrets are ignored by Git. They are intentionally local/rebuildable artifacts.

## Organized Output Artifacts

The previous flat `outputs/` directory has been grouped under:

```text
outputs/v9_corpus_build/
```

| Subdirectory | Contents |
| --- | --- |
| `audits/baseline/` | Full and partial baseline compile audits |
| `audits/targeted/` | Targeted repair/audit experiments |
| `audits/debug/` | Small focused debug audits |
| `datasets/` | Builder-native dataset backups and pilot runs |
| `debug_workdirs/` | Kept WSL/debug compile workdirs |
| `local_build/` | Local Vitis `Work/` build output |
| `logs/` | Scrape, shard, run, and compiler logs |
| `manifests/` | Compile-clean project manifests |
| `reports/` | Corpus reports and extra-candidate reports |
| `shards/` | v9 shard outputs used for the final merge |

## Main Scripts

| Script | Purpose |
| --- | --- |
| `scripts/validate_aie_compile.py` | Core compile validator for AIE/AIE-ML projects |
| `scripts/run_v7_parallel.py` | Parallel shard runner for dataset generation |
| `scripts/build_v7_bug_dataset.py` | Dataset materializer and mutator application engine |
| `scripts/merge_v7_datasets.py` | Merges shard outputs into train/validation/all JSONL files |
| `scripts/audit_v9_baselines.py` | Audits golden corpus baseline compile success |
| `scripts/hydrate_golden_missing_files.py` | Fetches missing repo-local headers/source files into corpus projects |
| `scripts/scrape_golden_aie_examples.py` | Scrapes candidate golden AIE examples |
| `scripts/repair_*.py` | Targeted corpus repair passes used to raise compile success |
| `bug famalies/generate_bug_families_bedrock.py` | Generates bug-family definitions and Python mutators |

The script names still include `v7` in a few places because the builder lineage started there. The current dataset artifact is v9.

## Environment

### Python

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install boto3 requests tqdm
```

### Secrets

Do not commit local tokens or Bedrock credentials.

Expected local secret locations:

```text
.venv/.env
bug famalies/secrets.json
```

### Vitis / WSL

Compile validation is designed to run through WSL with the Vitis AIE toolchain installed and available inside the Linux environment. The validator backend used for v9 was:

```text
--validator-backend wsl
```

## Reproduce The v9 Dataset Build

The v9 run used the compile-clean project manifest generated after corpus hydration and repair:

```text
outputs/v9_corpus_build/manifests/compile_clean_v9_toward200_projects.txt
```

The high-throughput build command was:

```powershell
python scripts\run_v7_parallel.py `
  --corpus-root "golden repos" `
  --out-dir "outputs\v9_dataset_40variants" `
  --project-list "outputs\v9_corpus_build\manifests\compile_clean_v9_toward200_projects.txt" `
  --shards 16 `
  --total-workers 32 `
  --variants-per-project 40 `
  --min-bugs 1 `
  --max-bugs 4 `
  --mutation-source generated `
  --generated-mutator-dir "bug famalies\generated\mutators" `
  --validator-backend wsl `
  --timeout 120 `
  --no-resume `
  --keep-shards
```

After merging, the final v9 files were moved to:

```text
data/processed/v9_dataset_40variants/
```

## Useful Checks

Count rows:

```powershell
Get-Content data\processed\v9_dataset_40variants\aie_instruction_v9_all.jsonl |
  Measure-Object -Line
```

Inspect the summary:

```powershell
Get-Content data\processed\v9_dataset_40variants\manifest_summary_v9.json
```

Check workspace state:

```powershell
git status --short
```

## Cleanup Notes

The repository has historical v5/v6 data deletions already visible in Git status from earlier work. The cleanup in this pass did not restore or remove those tracked historical files; it only organized current generated artifacts and documented the v9 layout.

The following paths are intentionally kept stable because scripts reference them directly:

```text
golden repos/
bug famalies/
scripts/
data/processed/v9_dataset_40variants/
```

If you rename those, update script defaults and README commands at the same time.

## Provenance

See `CITATIONS.md` for source-family notes, AMD/Xilinx documentation references, and repository provenance used to guide the dataset taxonomy.
