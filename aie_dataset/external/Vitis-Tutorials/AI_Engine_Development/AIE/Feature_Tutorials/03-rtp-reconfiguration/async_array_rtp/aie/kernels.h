/*
SOURCE: Xilinx/Vitis-Tutorials, branch 2024.2
PATH: AI_Engine_Development/AIE/Feature_Tutorials/03-rtp-reconfiguration/async_array_rtp/aie/kernels.h
DOMAIN: Runtime Parameter Reconfiguration
INTERFACE: Window
KEY INTRINSICS: Unknown
VECTOR TYPES: Unknown
*/

/*
Copyright (C) 2023, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
*/
#ifndef __KERNELS_H__
#define __KERNELS_H__

#include <adf.h>
using namespace adf;
template<int32 NUM>
void vect_add(input_buffer<int32,extents<NUM>>& __restrict in,output_buffer<int32,extents<NUM>>& __restrict out,const int32 (&value)[NUM]);

#endif /* __DDS_H__ */


