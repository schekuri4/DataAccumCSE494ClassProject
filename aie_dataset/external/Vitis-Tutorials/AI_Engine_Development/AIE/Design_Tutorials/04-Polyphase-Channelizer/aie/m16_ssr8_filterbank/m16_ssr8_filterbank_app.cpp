/*
SOURCE: Xilinx/Vitis-Tutorials, branch 2024.2
PATH: AI_Engine_Development/AIE/Design_Tutorials/04-Polyphase-Channelizer/aie/m16_ssr8_filterbank/m16_ssr8_filterbank_app.cpp
DOMAIN: DSP — FIR / Filtering
INTERFACE: Unknown
KEY INTRINSICS: Unknown
VECTOR TYPES: Unknown
*/

//
// Copyright (C) 2023, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Author: Mark Rollins

#include "m16_ssr8_filterbank_only_graph.h"

// Instantiate AIE graph:
m16_ssr8_filterbank_only_graph aie_dut;

// Initialize and run the graph:
int main(void)
{
  aie_dut.init();
  aie_dut.run(2); // Need 2 graph iterations since Nsamp_i/NSAMP = 16K/8192 = 2
  aie_dut.end();

  return 0;
}
