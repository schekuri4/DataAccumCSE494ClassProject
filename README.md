# DataAccum CSE494 Class Project

This repository contains a domain-specific dataset and tooling pipeline for training and evaluating large language models on AMD/Xilinx Versal AI Engine (AIE) debugging tasks. The project focuses on compile-grounded bug repair for AIE kernels and ADF graphs, where each example provides buggy source code, real Vitis compiler diagnostics, and a target repair in unified diff format.

The current dataset version is **v6**, built from a combination of curated AIE source material, LLM-generated correct baselines, targeted bug injection, and validation with the real Vitis AIE toolchain.

## Project Goals

- Build a supervised fine-tuning dataset for Versal AIE code repair.
- Cover AIE-specific failure modes that general code datasets rarely include.
- Use compiler validation instead of relying only on LLM-generated assumptions.
- Train models to return focused unified diffs rather than full rewritten files.
- Support paper-ready methodology, dataset statistics, and reproducible evaluation artifacts.

## Current Dataset: v6

| Metric | Value |
|---|---:|
| Total samples | 9,063 |
| Training samples | 8,011 |
| Validation samples | 1,052 |
| Unique bug types | 123 |
| Unique bug categories | 15 |
| Bedrock compile-bug rows | 7,702 |
| Unique Bedrock bug labels | 565 |
| Rows with real compiler logs | 9,063 |
| Unified diff repair targets | 9,063 |

The dataset files are stored in `data/processed/v6/`:

| File | Description |
|---|---|
| `aie_instruction_v6_all.jsonl` | Full v6 instruction dataset |
| `aie_instruction_v6_train.jsonl` | Training split |
| `aie_instruction_v6_validation.jsonl` | Validation split |
| `1 example` | Readable representative example row |

Each JSONL row follows the same high-level structure:

```json
{
  "instruction": "A Versal AIE build is failing with the error below. Return a unified diff that resolves it.",
  "context": "Buggy source code plus the real compiler error log.",
  "response": "A unified diff that repairs the bug.",
  "metadata": {
    "split": "train or validation",
    "bug_type": "...",
    "category": "...",
    "response_format": "unified_diff",
    "has_real_error_log": true
  }
}
```

## Methodology Overview

The dataset was built using a generate, validate, mutate, and materialize workflow.

1. **Generate correct AIE baselines**
   - LLM prompts create compact AIE mini-projects containing a graph file and kernel source file.
   - Prompts constrain the model to valid AIE APIs, realistic graph structure, and mutation-friendly code.

2. **Compile-validate correct projects**
   - Candidate baselines are compiled with the Vitis AIE toolchain.
   - Only compile-valid baselines are accepted as correct references.

3. **Inject targeted compile-time bugs**
   - A separate mutation prompt introduces one realistic AIE bug into each accepted baseline.
   - Bugs cover stream/window interfaces, graph wiring, vector intrinsics, accumulator types, template arguments, missing headers, and related AIE-specific compiler failures.

4. **Validate buggy projects**
   - Mutated projects must fail compilation.
   - The real compiler error log is captured and stored with the training example.

5. **Create instruction rows**
   - The final target response is computed as a unified diff from buggy code to the compile-valid correction.
   - This teaches models to produce targeted patches that can be applied and recompiled.

## Validation Tooling

The active validation flow is centered on:

| Script | Purpose |
|---|---|
| `scripts/validate_aie_compile.py` | Core AIE compile-validation driver |
| `scripts/run_validate_wsl.sh` | WSL entrypoint for running validation with Vitis |
| `scripts/bedrock_fix_compile_failures.py` | Bedrock-assisted repair pass for compile failures |
| `scripts/enrich_v5_with_wsl_errors.py` | Adds WSL compiler diagnostics to v5-derived rows |
| `scripts/train_unsloth_windows.py` | Local Windows fine-tuning helper |
| `scripts/setup_unsloth_windows.ps1` | Windows setup helper for Unsloth training |

Older v3, v4, and v5 pipeline scripts have been moved into `scripts/_archive/` so the active scripts directory stays focused.

## AIE Mini-Project Format

Generated and validated examples use a compact two-file AIE project layout:

```text
// FILE: graph.h
// ADF graph definition, PLIO/GMIO ports, kernels, and connections

// FILE: kernels/<name>.cc
// AIE kernel implementation using stream, window, vector, or accumulator APIs
```

The graph side typically includes `adf::graph`, `adf::kernel::create`, `adf::source`, `adf::connect`, and `adf::runtime<adf::ratio>`. Kernel files use AIE headers and APIs such as `readincr`, `writeincr`, `readincr_v`, `writeincr_v`, `aie::load_v`, `aie::mul`, `aie::accum`, and related vector operations.

## Example Training Task

```text
Instruction:
A Versal AIE build is failing with the error below. Return a unified diff that resolves it.

Compiler errors:
/tmp/aie_validate/job_359_62d1539c/kernel.cc:17:36: error: no matching function for call to 'broadcast'
/tmp/aie_validate/job_359_62d1539c/kernel.cc:26:17: error: no matching function for call to 'shuffle_up'

Expected response:
A unified diff that changes the invalid accumulator initialization and replaces the invalid shuffle call with the correct AIE API usage.
```

A full readable version of this example is available at `data/processed/v6/1 example`.

## Source Corpus and Provenance

The project uses curated AIE material from official AMD/Xilinx sources, research repositories, local examples, and synthetic generation. Source references and inclusion notes are documented in `CITATIONS.md`.

Important source families include:

- AMD/Xilinx Vitis tutorials and in-depth tutorials.
- Vitis Libraries and Model Composer examples.
- Research and community repositories such as Aries, AIM, SSR, GAMA, MaxEVA, polyaie, onnx2versal, iree-amd-aie, and MLIR-AIE-derived examples.
- Local hand-authored debug pairs for deadlock-style and out-of-bounds-style failures.

The curated source index in `aie_dataset/DATASET_INDEX.md` summarizes available examples across FIR filters, beamforming graphs, GEMM kernels, stream interfaces, window interfaces, cascade streams, GMIO, PLIO, and vector datatype coverage.

## Repository Structure

```text
aie_dataset/              Curated AIE examples and debug pairs
axolotl/                  Axolotl fine-tuning configs and notes
data/processed/v6/        Current v6 instruction dataset
scripts/                  Active validation, repair, and training scripts
scripts/_archive/         Archived historical pipeline scripts
unsloth/                  Local Windows fine-tuning notes
Work/                     Local Vitis build outputs, ignored by git
outputs/                  Local model/checkpoint outputs, ignored by git
```

## Environment Setup

### Python

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install boto3 requests tqdm
```

### Vitis AIE Validation

Validation is designed to run through WSL with Vitis 2025.2 available inside Ubuntu. The repository uses `scripts/run_validate_wsl.sh` as the bridge into the Linux validation environment.

```bash
bash scripts/run_validate_wsl.sh --help
```

### Amazon Bedrock

Bedrock-backed generation and repair scripts require a bearer token in the environment:

```powershell
$env:AWS_BEARER_TOKEN_BEDROCK = "<your-bedrock-token>"
```

## Fine-Tuning

Fine-tuning configuration files are available in `axolotl/` for Qwen2.5-Coder-based SFT and QLoRA runs. Local Windows fine-tuning notes are available in `unsloth/LOCAL_WINDOWS_FINE_TUNE.md`.

## Notes on Large Artifacts

Large generated archives, local build products, model checkpoints, and compiler outputs are intentionally ignored where they exceed normal GitHub repository limits or are reproducible from scripts. The current v6 JSONL dataset files are kept in `data/processed/v6/` for project reproducibility.

## Citations

See `CITATIONS.md` for repository provenance, source filtering rules, synthetic data notes, and AMD/Xilinx documentation references used to guide the dataset taxonomy and methodology.