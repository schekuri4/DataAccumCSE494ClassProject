# Dataset Citations and Provenance

This document lists the source categories used for the current successful corpus build.

## Current Corpus Composition

The current expanded source corpus contains:

- `2,437` real scraped entries
- `557` synthetic entries
- `2,994` combined entries

The processed instruction dataset derived from this corpus contains:

- `15,262` total rows
- `12,261` train rows
- `3,001` validation rows
- `3,152` unique source groups

## Real Repositories Used

### Official AMD / Xilinx repositories

| Repository                     | URL                                               | Notes                                                                        |
| ------------------------------ | ------------------------------------------------- | ---------------------------------------------------------------------------- |
| Xilinx/Vitis-Tutorials         | https://github.com/Xilinx/Vitis-Tutorials         | Branches scanned: `2022.1`, `2022.2`, `2023.1`, `2023.2`, `2024.1`, `2024.2` |
| Xilinx/Vitis-In-Depth-Tutorial | https://github.com/Xilinx/Vitis-In-Depth-Tutorial | Additional AIE tutorial examples and graph flows                             |
| Xilinx/Vitis_Accel_Examples    | https://github.com/Xilinx/Vitis_Accel_Examples    | AIE accelerator examples and integration patterns                            |
| Xilinx/Vitis-AI                | https://github.com/Xilinx/Vitis-AI                | Versal AI deployment code and related preprocessing logic                    |
| Xilinx/XRT                     | https://github.com/Xilinx/XRT                     | Runtime-side integration patterns touching AIE deployments                   |
| Xilinx/embeddedsw              | https://github.com/Xilinx/embeddedsw              | Embedded-side support code relevant to Versal platforms                      |
| Xilinx/Vitis_Libraries         | https://github.com/Xilinx/Vitis_Libraries         | Targeted subtrees: `dsp/`, `data_compression/`, `solver/`, `blas/`           |
| Xilinx/Vitis_Model_Composer    | https://github.com/Xilinx/Vitis_Model_Composer    | Model Composer AIE examples and graph wiring patterns                        |
| AMD/RyzenAI-SW                 | https://github.com/AMD/RyzenAI-SW                 | AMD software stack containing AIE-adjacent deployment code                   |

### Research and community repositories

| Repository                           | URL                                                     | Notes                                                         |
| ------------------------------------ | ------------------------------------------------------- | ------------------------------------------------------------- |
| arc-research-lab/Aries               | https://github.com/arc-research-lab/Aries               | MLIR and generated AIE intrinsic code                         |
| arc-research-lab/AIM                 | https://github.com/arc-research-lab/AIM                 | AIE kernels and graph structures for accelerator applications |
| arc-research-lab/SSR                 | https://github.com/arc-research-lab/SSR                 | Generated AIE intrinsic code for transformer-style workloads  |
| advent-lab/GAMA                      | https://github.com/advent-lab/GAMA                      | AIE-ML GEMM-oriented examples                                 |
| enyac-group/MaxEVA                   | https://github.com/enyac-group/MaxEVA                   | INT8 and FP32 AIE GEMM kernels                                |
| hanchenye/polyaie                    | https://github.com/hanchenye/polyaie                    | Polyhedral and compiler-driven AIE examples                   |
| rehohoho/onnx2versal                 | https://github.com/rehohoho/onnx2versal                 | ONNX-to-Versal kernels and graph scaffolding                  |
| Paolo309/XOHW-23-Versal-Registration | https://github.com/Paolo309/XOHW-23-Versal-Registration | Project-scale AIE kernels for image registration              |
| nod-ai/iree-amd-aie                  | https://github.com/nod-ai/iree-amd-aie                  | Compiler/runtime code for AMD AIE targets                     |
| pjh177787/my_mlir-aie                | https://github.com/pjh177787/my_mlir-aie                | Additional mlir-aie-derived examples                          |

## Filtering and Inclusion Rules

Real source files were filtered to AIE-relevant C and C++ source files:

- extensions: `.cc`, `.cpp`, `.cxx`, `.h`, `.hpp`, `.hh`
- path hints such as `aie`, `graph`, `kernel`, `versal`, `gmio`, `plio`, `fir`, `fft`, `gemm`, `demod`, `cfar`
- content markers such as `aie::vector`, `input_buffer`, `output_buffer`, `input_stream`, `output_stream`, `readincr`, `writeincr`, `adf::graph`, `adf::connect`, `adf::PLIO`, `adf::GMIO`, `chess_prepare_for_pipelining`

Files were deduplicated by normalized content hash across repositories and branches.

## GitHub Search Status

The final successful corpus for this repository was built without GitHub code-search discovery.

Reason:

- curated repo crawl completed successfully and provided enough data
- GitHub code search hit authenticated rate limits during later expansion attempts
- the final corpus was intentionally finalized from the cached curated crawl plus synthetic generation rather than waiting for search resets

## Synthetic Data Sources

Synthetic files were produced by `scripts/build_aie_source_corpus.py`. They are generated source files, not copied vendor files.

They cover:

- correct kernels
- correct graphs
- buggy kernels
- buggy graphs

The synthetic corpus fills gaps in:

- data types
- vector widths
- interface combinations
- graph topologies
- common AIE bug modes

## Debug Pair Derived Files

The repository also contains hand-constructed debug-pair examples derived from existing AIE sources:

| File                                                     | Derived From                                      | Change                                                              |
| -------------------------------------------------------- | ------------------------------------------------- | ------------------------------------------------------------------- |
| `aie_dataset/debug_pairs/data_shuffle_BUGGY_deadlock.cc` | `Xilinx/Vitis-Tutorials` debug walkthrough source | Output/input balance modified to induce a deadlock-style failure    |
| `aie_dataset/debug_pairs/peak_detect_BUGGY_oob.cc`       | `Xilinx/Vitis-Tutorials` debug walkthrough source | Iteration/indexing changed to create an out-of-bounds style failure |

## Reference Documentation Used for Taxonomy and Bug Design

These documents were used as technical references for bug categories, AIE terminology, and interface constraints. They were not copied verbatim into the dataset.

| Document                                             | URL                                                                                                                    |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| UG1076: AI Engine Tools and Flows User Guide         | https://docs.amd.com/r/en-US/ug1076-ai-engine-environment                                                              |
| UG1079: AI Engine Kernel and Graph Programming Guide | https://docs.amd.com/r/en-US/ug1079-ai-engine-kernel-coding/Summary                                                    |
| UG1076 Deadlock Detection                            | https://docs.amd.com/r/en-US/ug1076-ai-engine-environment/Deadlock-Detection                                           |
| UG1076 Lock Stall Analysis                           | https://docs.amd.com/r/en-US/ug1076-ai-engine-environment/Lock-Stall-Analysis                                          |
| UG1076 AI Engine Compiler Guidance                   | https://docs.amd.com/r/2024.1-English/ug1076-ai-engine-environment/AI-Engine-Compiler-Guidance                         |
| UG1079 Buffer Location Constraints                   | https://docs.amd.com/r/en-US/ug1079-ai-engine-kernel-coding/Buffer-Location-Constraints                                |
| UG1079 AI Engine API                                 | https://docs.amd.com/r/en-US/ug1079-ai-engine-kernel-coding/AI-Engine-API                                              |
| UG1079 PDF v2022.2                                   | https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2022_2/ug1079-ai-engine-kernel-coding.pdf |

## Project-Owned Code

The following files are original project code used to fetch, expand, and build the dataset:

- `scripts/fetch_aie_sources.py`
- `scripts/build_aie_source_corpus.py`
- `scripts/build_aie_instruction_dataset.py`
- `scripts/build_axolotl_dataset.py`
- `scripts/train_unsloth_windows.py`
- `axolotl/aie_instruction_sft.yml`
- `axolotl/aie_sft.yml`
