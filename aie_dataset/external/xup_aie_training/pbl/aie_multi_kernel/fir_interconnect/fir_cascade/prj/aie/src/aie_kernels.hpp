/*
SOURCE: Xilinx/xup_aie_training, branch main
PATH: pbl/aie_multi_kernel/fir_interconnect/fir_cascade/prj/aie/src/aie_kernels.hpp
DOMAIN: DSP — FIR / Filtering
INTERFACE: Stream
KEY INTRINSICS: Unknown
VECTOR TYPES: Unknown
*/

// Copyright (C) 2023 Advanced Micro Devices, Inc
//
// SPDX-License-Identifier: MIT

#ifndef __KERNELS_H__
#define __KERNELS_H__

#include <adf/stream/types.h>

// Kernel prototype
void fir_8t_16int_cascade_in(input_stream_acc48 * cascade_in, output_stream_int16 *  out);

void fir_8t_16int_cascade_out(input_stream_int16 * in, output_stream_acc48 * cascade_out);


#endif /**********__KERNELS_H__**********/
