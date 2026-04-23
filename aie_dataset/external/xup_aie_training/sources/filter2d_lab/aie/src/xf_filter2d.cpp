/*
SOURCE: Xilinx/xup_aie_training, branch main
PATH: sources/filter2d_lab/aie/src/xf_filter2d.cpp
DOMAIN: DSP — FIR / Filtering
INTERFACE: Window
KEY INTRINSICS: aie::filter2D_k3_border
VECTOR TYPES: Unknown
*/

// Copyright (C) 2023 Advanced Micro Devices, Inc
//
// SPDX-License-Identifier: MIT

#include "imgproc/xf_filter2d_16b_aie.hpp"
#include "kernel.hpp"

/*
Floating point values of the kernel
kData[9] = {0.0625, 0.125, 0.0625, 0.125, 0.25, 0.125, 0.0625, 0.125, 0.0625};
*/

void filter2D(input_window_int16* input, output_window_int16* output) {
    const int16_t coeff[16] = {64, 128, 0, 64, 0, 128, 256, 0, 128, 0, 64, 128, 0, 64, 0, 0};
    xf::cv::aie::filter2D_k3_border(input, coeff, output);
};