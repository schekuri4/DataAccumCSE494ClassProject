/*
SOURCE: Xilinx/Vitis-Tutorials, branch 2024.2
PATH: AI_Engine_Development/AIE/Feature_Tutorials/02-using-gmio/single_aie_gmio/step1/aie/kernel.h
DOMAIN: GMIO / Data Movement
INTERFACE: Window
KEY INTRINSICS: Unknown
VECTOR TYPES: Unknown
*/

/*
Copyright (C) 2023, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
*/
#ifndef __KERNEL_H__
#define __KERNEL_H__
#include <adf.h>
using namespace adf;
void weighted_sum_with_margin(input_buffer<int32,extents<256>,margin<8>> & in, output_buffer<int32,extents<256>> & out);
#endif
