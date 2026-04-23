/*
SOURCE: Xilinx/xup_aie_training, branch main
PATH: sources/filter2d_lab/aie/src/kernel.hpp
DOMAIN: DSP — FIR / Filtering
INTERFACE: Window
KEY INTRINSICS: Unknown
VECTOR TYPES: Unknown
*/

// Copyright (C) 2023 Advanced Micro Devices, Inc
//
// SPDX-License-Identifier: MIT

#ifndef _KERNELS_16B_H_
#define _KERNELS_16B_H_

#include <adf/window/types.h>
#include <adf/stream/types.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

void filter2D(input_window_int16* input, output_window_int16* output);

#endif