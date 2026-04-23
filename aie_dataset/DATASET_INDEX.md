# AIE Debug Dataset — Index

Collected from official AMD/Xilinx open-source repositories (MIT license).
All files are real production code from Vitis-Tutorials 2024.2 and Xilinx example repos.

---

## Directory Structure

```
aie_dataset/
├── debug_pairs/          ← Bug/fix pairs for debug training (HIGHEST PRIORITY)
├── dsp_fir/              ← FIR filter and DSP kernels (Stream + Window interfaces)
├── dsp_beamforming/      ← 5G NR beamforming (Cascade stream, location constraints)
├── matrix_ops/           ← GEMM (GMIO, Window, Cascade stream)
└── graphs/               ← Standalone ADF graph headers
```

---

## debug_pairs/ — Error-Correction Training Pairs

| File | Type | Bug / Description |
|------|------|-------------------|
| `peak_detect.cc` | CORRECT kernel | Input: `input_buffer<int32>` + `output_stream_int32`. Uses `aie::reduce_max`, `aie::mul`, `aie::to_float`, `writeincr`. 16-lane `v<int32,16>` |
| `upscale.cc` | CORRECT kernel | Input: `input_buffer<float>` + `input_stream_int32`. Uses `readincr`, `aie::mul`, conditional scale logic |
| `data_shuffle_CORRECT.cc` | CORRECT kernel | Stream consumer. `readincr(outmax)` called every 2 iterations — critical for deadlock prevention |
| `data_shuffle_BUGGY_deadlock.cc` | **BUGGY** | `readincr(outmax)` removed → stream tokens accumulate → `peak_detect` blocks. Symptom: `Lock_East stall at T=575600 ps`. Fix: restore the `readincr` call |
| `peak_detect_BUGGY_oob.cc` | **BUGGY** | `*(InIter+8500000500)` — massive out-of-bounds offset. Detection: `aiesimulator --enable-memory-check`. Fix: use `*InIter++` |

**Debug commands:**
```bash
aiesimulator --pkg-dir=./Work --hang-detect-time=60        # deadlock
aiesimulator --pkg-dir=./Work --enable-memory-check        # OOB
vitis_analyzer aiesimulator_output/default.aierun_summary  # analysis
```

---

## dsp_fir/ — FIR Filters

| File | Source | Interface | Key Content |
|------|--------|-----------|-------------|
| `FirSingleStream.cpp` | Vitis-Tutorials 02-SSR-FIR | **Stream** (`input_stream_cint16`) | Classic low-level intrinsics: `mul4`, `mac4`, `readincr_v4`, `writeincr_v4`, `upd_v`, `srs`. `v8cint16`, `v32cint16`, `v4cacc48`. `chess_loop_range`, `chess_pipeline_adjust_preamble` |
| `FirSingleStream_graph.h` | Vitis-Tutorials 02-SSR-FIR | Stream PLIO 500 MHz | `connect<stream>`, `kernel::create_object<>()` with taps vector, `cint16` coefficients |
| `softdemod_kernel.cpp` | Vitis-Tutorials 25-kernel-opt | **Window** (`adf::input_buffer<cfloat>`) | Modern `aie_api`: `aie::load_v`, `aie::sub`, `aie::abs_square`, `aie::mac`, `fpadd` with shuffle masks, `inv()`, multi-pass scratch buffer pattern |
| `softdemod_graph.h` | Vitis-Tutorials 25-kernel-opt | PLIO 625 MHz | `kernel::create_object<>()` with constructor args, conditional `_DUMPLOOP_` output port |

---

## dsp_beamforming/ — 5G NR Beamforming

| File | Source | Interface | Key Content |
|------|--------|-----------|-------------|
| `subsys_graph.h` | Vitis-Tutorials 03-beamforming | **Cascade stream** + Window | Template graph `bfCascadeChain<xoff,yoff,len>`. `connect<cascade>`. `location<kernel>()`, `location<stack>()`, `location<buffer>()` placement constraints. `bank()` for memory bank control. `initialization_function()`. Hierarchical: `DL64A32L` (32 tiles) and `UL64A32L` (32 tiles) |

**Common debug issues in beamforming graphs:**
- Memory bank conflicts: two buffers assigned to same bank → aiecompiler placement failure
- Cascade stream ordering: must match physical tile adjacency (left-right or right-left per row parity)
- `runtime<ratio>` too high (>0.9) with cascade chains causes timing violations

---

## matrix_ops/ — GEMM

| File | Source | Interface | Key Content |
|------|--------|-----------|-------------|
| `xgemm_graph.h` | plnx-aie-examples | **GMIO** + Window + Cascade | `input_gmio`/`output_gmio` for DDR access. `connect<window<WIN_SIZE_BYTES>>` with `async()`. Serpentine data flow (even/odd row reversal). Dual AIE/AIE-ML architecture via `__AIE_ARCH__` macro |
| `two_inputs_gemm_kernel.cc` | plnx-aie-examples | Window + Cascade | `window_acquire`/`window_release`. `readincr_v<N>`, `writeincr`. `get_coreid()` for dynamic position. `aie::load_v`, `aie::store_unaligned_v`, `aie::mul`, `aie::reduce_add`, `acc80` accumulator |

---

## graphs/ — ADF Graph Headers

| File | Source | Topology |
|------|--------|----------|
| `converter_graph.h` | Vitis-Tutorials 09-debug-walkthrough | 1 input PLIO → `peak_detect` → `upscale` → output PLIO; `peak_detect` → `data_shuffle` → output PLIO. Mixed window+stream connections |

---

## Interface Coverage Summary

| Interface Type | Files |
|---------------|-------|
| Window (input_buffer / output_buffer) | `peak_detect.cc`, `upscale.cc`, `data_shuffle_*.cc`, `softdemod_kernel.cpp` |
| Stream (input/output_stream) | `FirSingleStream.cpp`, `data_shuffle_*.cc`, `peak_detect.cc` |
| Cascade stream | `subsys_graph.h`, `xgemm_graph.h`, `two_inputs_gemm_kernel.cc` |
| GMIO (DDR memory mapped) | `xgemm_graph.h`, `two_inputs_gemm_kernel.cc` |
| PLIO 64-bit | All graph.h files |

---

## Vector Type Coverage

| Type | File |
|------|------|
| `aie::vector<int32,16>` | `peak_detect.cc` |
| `aie::vector<float,16>` | `upscale.cc` |
| `aie::vector<int32,8>` | `data_shuffle_*.cc` |
| `aie::vector<cfloat,16>` | `softdemod_kernel.cpp` |
| `aie::vector<float,8>`, `aie::vector<int32,8>` | `softdemod_kernel.cpp` |
| `v8cint16`, `v32cint16`, `v4cacc48` | `FirSingleStream.cpp` (classic intrinsics) |
| `aie::vector<int32,VECTOR_LENGTH>`, `acc48`, `acc80` | `two_inputs_gemm_kernel.cc` |
| `cint16`, `cacc48` | `subsys_graph.h` (beamforming) |

---

## Next Steps for Dataset Expansion

1. **Fetch `bf8x8_fst.cc`** (beamforming kernel) — raw source available at:
   `https://raw.githubusercontent.com/Xilinx/Vitis-Tutorials/2024.2/AI_Engine_Development/AIE/Design_Tutorials/03-beamforming/Module_02_AI_Engine_Design/src/kernels/bf8x8_fst.cc`

2. **Add window sizing bug pairs** — create examples where `dimensions()` in graph.h
   does not match the loop count in the kernel (common hang cause)

3. **Add FFT examples** — from `05-Prime-Factor-FFT` or `06-fft2d_AIEvsHLS`

4. **Add UG1076 deadlock scenarios** as synthetic code pairs based on:
   `https://docs.amd.com/r/en-US/ug1076-ai-engine-environment/Deadlock-Detection`

5. **AIE-BLAS kernels** — available at:
   `https://github.com/atlarge-research/AIE-BLAS` (kernels/ subdirectory)
