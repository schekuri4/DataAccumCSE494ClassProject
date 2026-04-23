/*
Copyright (C) 2023, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT

SOURCE: Xilinx/Vitis-Tutorials, branch 2024.2 (synthetically introduced bug)
DOMAIN: Debug — Out-of-Bounds Memory Access
BUG TYPE: Out-of-bounds buffer read
SYMPTOM: Memory access violation detected at runtime
DETECTION: aiesimulator --pkg-dir=./Work --enable-memory-check
           Valgrind (x86 simulation target)
           Compiler guidance report: Work/reports/guidance.html
FIX: Remove the invalid offset "+8500000500". Replace with:
         v_in = *InIter++;
     The buffer iterator aie::begin_vector<16>(inx) should advance by +1 each
     iteration, not jump to an address outside the tile's local memory range.
*/

#include "kernels.h"

void peak_detect(
    input_buffer<int32> & __restrict inx,
    output_buffer<int32> & __restrict to_next,
    output_stream_int32 * __restrict outmax,
    output_buffer<float> & __restrict outmin_v
)
{
	auto InIter = aie::begin_vector<16>(inx);
	auto OutIter_1 = aie::begin_vector<16>(to_next);
	auto OutIter_2 = aie::begin_vector<16>(outmin_v);

	aie::vector<int32,16> v_in = aie::zeros<int32,16>();
	aie::accum<accfloat,16> res = aie::zeros<accfloat,16>();
	aie::accum<accfloat,16> mul_pi = aie::zeros<accfloat,16>();

	for(int i=0;i<NUM_SAMPLES;i++) {

		v_in = *(InIter+8500000500);  // BUG: massively out-of-bounds offset

		int32 max_out = aie::reduce_max(v_in);

		mul_pi = aie::mul(aie::to_float(v_in),(PIE_VALUE));
		int32 min_m = aie::reduce_min(v_in);
		res = aie::add(mul_pi,aie::to_float(min_m));

		*OutIter_1++ = v_in;
		writeincr(outmax,max_out);
		*OutIter_2++ = res.to_vector<float>(0);
	}
}
