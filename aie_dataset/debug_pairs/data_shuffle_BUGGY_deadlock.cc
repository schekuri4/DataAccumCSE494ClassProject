/*
Copyright (C) 2023, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT

SOURCE: Xilinx/Vitis-Tutorials, branch 2024.2 (synthetically introduced bug)
DOMAIN: Debug — Stream Deadlock
BUG TYPE: Stream consumption mismatch
SYMPTOM: Lock_East stall at T=575600.000000 ps in aiesimulator
         Graph never produces output on out_data_shuffle port.
DETECTION: aiesimulator --pkg-dir=./Work --hang-detect-time=60
           vitis_analyzer shows Lock Stall >90% on data_shuffle tile
FIX: Restore the readincr(outmax) call inside the if(remainder==0) block.
     The peak_detect kernel emits one int32 per 16 input elements (via writeincr),
     so data_shuffle must read one value every 2 loop iterations to maintain balance.
*/

#include "kernels.h"

void data_shuffle(
    input_buffer<int32> & __restrict from_prev,
    input_stream_int32 * __restrict outmax,   // <-- stream is never consumed: DEADLOCK
    output_buffer<int32> & __restrict out_shift
)
{
	auto InIter = aie::begin_vector<8>(from_prev);
	auto OutIter = aie::begin_vector<8>(out_shift);
	aie::vector<int32,8> prev_in = aie::zeros<int32,8>();
	aie::vector<int32,8> shuffle_t = aie::zeros<int32,8>();
	int32 max_scalar = 0;  // uninitialized but never updated from stream

	for(int i=0;i<NUM_SAMPLES*2;i++)
	{
		prev_in = *InIter++;
		int remainder = i & 1;
		// BUG: readincr(outmax) removed — stream tokens accumulate, peak_detect blocks
		// if(remainder == 0)
		//     max_scalar = readincr(outmax);
		aie::vector<int32,8> max_vector = aie::broadcast<int32,8>(max_scalar);
		shuffle_t = aie::shuffle_up_fill(prev_in,max_vector,4);
		*OutIter++ = shuffle_t;
	}
}
