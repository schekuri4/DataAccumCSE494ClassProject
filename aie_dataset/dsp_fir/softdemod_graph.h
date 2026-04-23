//
// Copyright (C) 2024, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Author: Richard Buz
//
// SOURCE: Xilinx/Vitis-Tutorials, branch 2024.2
// PATH: AI_Engine_Development/AIE/Feature_Tutorials/25-AIE-kernel-optimization/labA/src/softdemod_graph.h
// DOMAIN: DSP — Soft Demodulation
// INTERFACE: PLIO (plio_64_bits, 625 MHz), Window buffers
// KEY PATTERNS:
//   - kernel::create_object<>() with constructor arguments (constellation, scratch buffers)
//   - Conditional output port via _DUMPLOOP_ macro (debug instrumentation pattern)
//   - High-frequency PLIO: 625.0 MHz clock specification
//   - runtime<ratio> = 0.9
//   - Single-kernel graph with explicit source() path

#pragma once

#include <adf.h>
#include "softdemod_kernel.h"
#include "config.h"
#include "mod_const.h"

using namespace adf;

class softdemod_graph : public adf::graph {
private:
  kernel softdemod_krnl;

public:
  input_plio iplio;
  output_plio oplio;
#ifdef _DUMPLOOP_
  output_plio loopplio;
#endif

  softdemod_graph()
  {
      iplio = input_plio::create("DIN",  plio_64_bits, "data/input_softdemod.txt",  625.0);
      oplio = output_plio::create("DOUT", plio_64_bits, "data/output_softdemod_aie.txt", 625.0);
#ifdef _DUMPLOOP_
      loopplio = output_plio::create("TSTOUT", plio_64_bits, "data/internal_loop_aie.txt", 625.0);
#endif

      softdemod_krnl = kernel::create_object<softdemod_kernel>(
          std::vector<cfloat>{APSK16},
          std::vector<cfloat>(16*BUFSZ),
          std::vector<float>(16*BUFSZ),
          std::vector<float>(16*BUFSZ));

      connect(iplio.out[0], softdemod_krnl.in[0]);
      connect(softdemod_krnl.out[0], oplio.in[0]);
#ifdef _DUMPLOOP_
      connect(softdemod_krnl.out[1], loopplio.in[0]);
#endif

      source(softdemod_krnl) = "src/softdemod_kernel.cpp";

      runtime<ratio>(softdemod_krnl) = 0.9;
  }
};
