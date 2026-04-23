/*
SOURCE: Xilinx/xup_aie_training, branch main
PATH: pbl/aie_single_kernel/fir_lowpass/aie/src/aie_kernels.h
DOMAIN: DSP — FIR / Filtering
INTERFACE: Window
KEY INTRINSICS: Unknown
VECTOR TYPES: Unknown
*/

// Copyright (C) 2023 Advanced Micro Devices, Inc
//
// SPDX-License-Identifier: MIT

#pragma once

#include <adf.h>

#include <aie_api/aie.hpp>
#include <aie_api/utils.hpp>
#include <aie_api/aie_adf.hpp>



void fir_16taps_symm(
        input_window<cint16> * cb_input,
        output_window<cint16> * cb_output
);

