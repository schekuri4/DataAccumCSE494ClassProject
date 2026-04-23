# DataAccum CSE494 Class Project

This repository builds a supervised fine-tuning dataset for AMD/Xilinx Versal AI Engine code understanding, debugging, and graph analysis.

The current corpus is built from two sources:

- Curated real AIE kernel and graph source files scraped from public GitHub repositories.
- Synthetic AIE kernels and graphs generated to fill coverage gaps and provide controlled bug variants.

The current processed dataset was rebuilt from the expanded corpus and contains:

- `10,642` total instruction rows
- `8,508` training rows
- `2,134` validation rows
- `3,615` unique source files processed
- `3,152` unique source groups
- `80.01%` bug-focused rows (explicit debug/fix supervision)
- `5,941` responses that teach a fix with a real unified `diff` block
- `226` distinct bug types, `201 / 205` taxonomy slugs covered
- Max single-repo share capped at `~19.5%` (no repo dominates)

### Tier distribution (bug-focused rows)

| Tier          | Rows  |
| ------------- | ----- |
| `normal`      | 3,371 |
| `easy`        | 2,126 |
| `hard`        | 920   |
| `medium`      | 499   |
| `extra_hard`  | 342   |

### Variant mix

| Variant                                   | Rows  |
| ----------------------------------------- | ----- |
| `bug_fix_pair`                            | 5,143 |
| `causal_debugging`                        | 1,185 |
| `structured_extraction`                   | 1,164 |
| `deep_explanation`                        | 1,149 |
| `taxonomy_inspection_negative`            | 872   |
| `multi_file_bug_fix_pair`                 | 711   |
| `taxonomy_multi_file_inspection_negative` | 331   |
| `taxonomy_debug_scenario`                 | 64    |
| `taxonomy_multi_file_debug_scenario`      | 23    |

## What This Repository Produces

The main outputs are:

- `data/raw/aie_github_sources.jsonl`
  Curated real-source corpus from public repositories.
- `data/raw/aie_synthetic_sources.jsonl`
  Synthetic AIE kernels, graphs, and bug variants.
- `data/raw/aie_expanded_sources.jsonl`
  Combined real and synthetic source corpus.
- `data/processed/aie_instruction_all.jsonl`
  Fully expanded instruction dataset.
- `data/processed/aie_instruction_train.jsonl`
  Training split.
- `data/processed/aie_instruction_validation.jsonl`
  Validation split.
- `data/processed/aie_sft_upload_ready.jsonl`
  Upload-ready copy of the instruction dataset.

## Data Provenance

The expanded source corpus currently contains:

- `2,437` scraped real entries
- `557` synthetic entries
- `2,994` combined source entries

The scraped corpus was finalized from the curated repository crawl only. GitHub code-search discovery was intentionally skipped for the final successful build because the rate-limited search phase was not necessary to reach a useful dataset size.

## Real Source Repositories Used

The current real-source corpus was built from cached scans of these repositories:

### AMD / Xilinx official repositories

- `Xilinx/Vitis-Tutorials`
  Branches scanned: `2022.1`, `2022.2`, `2023.1`, `2023.2`, `2024.1`, `2024.2`
- `Xilinx/Vitis-In-Depth-Tutorial`
- `Xilinx/Vitis_Accel_Examples`
- `Xilinx/Vitis-AI`
- `Xilinx/XRT`
- `Xilinx/embeddedsw`
- `Xilinx/Vitis_Libraries`
  Targeted subtrees: `dsp/`, `data_compression/`, `solver/`, `blas/`
- `Xilinx/Vitis_Model_Composer`
- `AMD/RyzenAI-SW`

### Research and community repositories

- `arc-research-lab/Aries`
- `arc-research-lab/AIM`
- `arc-research-lab/SSR`
- `advent-lab/GAMA`
- `enyac-group/MaxEVA`
- `hanchenye/polyaie`
- `rehohoho/onnx2versal`
- `Paolo309/XOHW-23-Versal-Registration`
- `nod-ai/iree-amd-aie`
- `pjh177787/my_mlir-aie`

These sources were filtered down to `.cc`, `.cpp`, `.h`, `.hpp`, `.cxx`, and `.hh` files that contain AIE- and ADF-relevant patterns such as `aie::vector`, `input_buffer`, `output_buffer`, `input_stream`, `readincr`, `writeincr`, `adf::graph`, `adf::connect`, `adf::PLIO`, `adf::GMIO`, and `chess_prepare_for_pipelining`.

Files are deduplicated by normalized content hash before inclusion in the raw JSONL corpus.

## Synthetic Source Generation

Synthetic files are used to extend coverage where the real corpus is thin. They are not copied from vendor code verbatim.

The synthetic generator currently emits:

- Correct kernels
- Correct graphs
- Buggy kernels
- Buggy graphs

Coverage includes:

- Data types: `int8`, `int16`, `int32`, `float`, `cint16`, `cint32`, `cfloat`
- Interfaces: buffer, stream, cascade, async, PLIO, GMIO
- Patterns such as FIR, FFT butterflies, matrix multiply, beamforming, decimation, interpolation, correlation, QAM demodulation, channel estimation, digital downconversion, CFAR, CORDIC, AGC, matched filtering, and related graph topologies

Bug variants include issues such as:

- token imbalance / deadlock
- buffer mismatch
- wrong vector lane width
- missing pipelining pragmas
- off-by-one iteration errors
- accumulator misuse
- PLIO width mismatches

## Debugging-Focused Supervision

The dataset is explicitly tuned to teach *debugging*, not just code explanation.
Roughly 80% of rows are bug-focused and most of those carry a **real unified
`diff`** showing the minimal fix. Key pieces:

- **~29 regex-based mutators** inject realistic, category-specific bugs into
  known-correct kernel and graph sources. Examples: stream deadlock, window
  OOB, missing iterator increment, `runtime<ratio>` zero / overflow, PLIO
  width mismatch, reversed `connect` direction, missing output write, wrong
  vector lane width, graph dimension mismatch, subtraction-for-addition,
  mul-to-add, `to_vector` shift-by-15, `acc48`-for-`acc80`, loop-count halved,
  window-size halved, removed accumulator zeros-init, broadcast width
  mismatch, duplicate stream read, dropped last sample, removed
  `chess_prepare_for_pipelining`, `readincr` from output, `break` in a
  pipelined loop, port-index OOB, missing `graph.wait()`, missing
  `adf::connect`, self-loop `connect`, wrong `to_vector` output type,
  missing `adf::source`, missing `runtime<ratio>`, window-margin-to-size,
  `bfloat16`-on-AIE1, signed/unsigned mismatch, unaligned load,
  modulo-in-loop, broken accumulator reset, non-power-of-two `begin_vector`.
- **Unified-diff responses** on every `bug_fix_pair` and
  `multi_file_bug_fix_pair` row, produced with `difflib`, anchored to the
  real buggy / correct file contents.
- **Real symptom strings** (not templated scaffolds) per bug category, e.g.
  *"aiecompiler reports tile overcommitted: sum of runtime ratios exceeds 1.0"*.
- **Explicit negative examples** (`taxonomy_inspection_negative` and
  `taxonomy_multi_file_inspection_negative`, 1,203 rows) where the model is
  asked whether a candidate bug is present and the correct verdict is
  `not_present` — this is anti-hallucination supervision.
- **Multi-file debugging** (`multi_file_bug_fix_pair`, 711 rows) where the
  root cause is cross-file and the model must reason across a buggy primary
  source, a related header/graph, and a reference-correct primary.
- **Tier-aware oversampling** so `hard` and `extra_hard` bugs are not
  drowned out by the more numerous `easy` / `normal` patterns.
- **Diversity caps**: per bug type, per source group, and per source repo
  (≤15% share) to prevent the model memorizing any one repo or template.

## Dataset Shape

Each processed row contains:

- `instruction`
- `context`
- `response`
- `metadata`

The builder creates several task variants per source, including:

- general analysis
- feature extraction
- debug risk analysis
- dataflow explanation
- bug-fix pair comparisons
- single-file and multi-file bug-fix diffs
- taxonomy inspection (both positive and *not-present* verdicts)
- causal debugging walkthroughs

This makes the final instruction count much larger than the raw source-file count.

## Rebuild Commands

### Rebuild the source corpus from cached curated repositories and synthetic generation

```powershell
& "c:/Users/schek/OneDrive/Desktop/494 project/.venv/Scripts/python.exe" scripts/build_aie_source_corpus.py all --skip-code-search --synthetic-kernels 100 --synthetic-graphs 40
```

### Rebuild the processed instruction dataset from the combined source JSONL

```powershell
& "c:/Users/schek/OneDrive/Desktop/494 project/.venv/Scripts/python.exe" scripts/build_aie_instruction_dataset.py --expanded-source-jsonl data/raw/aie_expanded_sources.jsonl
```

## Fine-Tuning Outputs

The repository also includes local fine-tuning support:

- `scripts/train_unsloth_windows.py`
- `scripts/setup_unsloth_windows.ps1`
- `unsloth/LOCAL_WINDOWS_FINE_TUNE.md`
- `axolotl/LOCAL_FINE_TUNE.md`

For the current Windows workflow, the practical local target is `Qwen/Qwen2.5-Coder-7B-Instruct` using the processed instruction dataset.

## Source Citations

See `CITATIONS.md` for the repository-by-repository provenance list used for the current corpus and the documentation references used for synthetic bug taxonomy design.
