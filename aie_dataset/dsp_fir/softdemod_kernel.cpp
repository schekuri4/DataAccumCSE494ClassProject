//
// Copyright (C) 2024, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Author: Richard Buz
//
// SOURCE: Xilinx/Vitis-Tutorials, branch 2024.2
// PATH: AI_Engine_Development/AIE/Feature_Tutorials/25-AIE-kernel-optimization/labA/src/softdemod_kernel.cpp
// DOMAIN: DSP — Soft Demodulation (16-QAM / APSK16)
// INTERFACE: Window (adf::input_buffer<cfloat>, adf::output_buffer<float>)
// KEY INTRINSICS: aie::load_v, aie::begin, aie::begin_restrict_vector, aie::sub,
//                 aie::abs_square, aie::mac, aie::to_fixed, aie::lt, aie::select,
//                 fpadd (with shuffle mask 0x67452301), inv(), aie::mul
// VECTOR TYPES: aie::vector<cfloat,16>, aie::vector<float,8>, aie::vector<int32,8>
//               aie::accum<accfloat,8>, aie::mask<8>
// CHESS PRAGMAS: chess_prepare_for_pipelining, chess_loop_count()
// OPTIMIZATION NOTE: Multi-pass algorithm using intermediate scratch buffers (wbufA, wbufB, wbufC).
//                    fpadd with shuffle masks performs tree-reduction of exp sums without scalar code.
//                    inv() used for final normalization (LLR computation).

#include "softdemod_kernel.h"
#include <adf.h>
#include <aie_api/aie.hpp>
#include <aie_api/aie_adf.hpp>

using namespace adf;

void softdemod_kernel::softdemod(adf::input_buffer<cfloat, extents<BUFSZ>>& __restrict in,
#ifndef _DUMPLOOP_
                                 adf::output_buffer<float, extents<4*BUFSZ>>& __restrict out)
#else
                                 adf::output_buffer<float, extents<4*BUFSZ>>& __restrict out,
                                 adf::output_buffer<cfloat, extents<16*BUFSZ>>& __restrict loopout)
#endif
{
    aie::vector<cfloat,16> ref_const = aie::load_v<16>(constel);
    auto pIn = aie::begin(in);
    auto pwbufA16 = aie::begin_restrict_vector<16>(wbufA);

    // Pass 1: subtract each input symbol from all 16 constellation points
    for (unsigned i = 0; i < BUFSZ; i++)
		    chess_prepare_for_pipelining
		    chess_loop_count(BUFSZ)
    {
        *pwbufA16++ = aie::sub(ref_const, *pIn++);
    }

    auto pwbufA8 = aie::begin_restrict_vector<8>(wbufA);
    auto pwbufB8 = aie::begin_restrict_vector<8>(wbufB);

    // Pass 2: compute squared magnitudes |d|^2
    for (unsigned i = 0; i < 2*BUFSZ; i++)
		    chess_prepare_for_pipelining
		    chess_loop_count(2*BUFSZ)
	  {
        *pwbufB8++ = aie::abs_square(*pwbufA8++);
    }

    pwbufB8 -= 2*BUFSZ;

    aie::accum<accfloat,8> acc_init;
    acc_init.from_vector(aie::broadcast<float,8>(EADD),0);
    auto pwbufC8 = aie::begin_restrict_vector<8>(wbufC);

    // Pass 3: compute exp(-|d|^2 * EMULT + EADD) approximation
    for (unsigned i = 0; i < 2*BUFSZ; i++)
		    chess_prepare_for_pipelining
		    chess_loop_count(2*BUFSZ)
	  {
        *pwbufC8++ = aie::mac(acc_init, *pwbufB8++, EMULT);
    }

    pwbufB8 -= 2*BUFSZ;
    pwbufC8 -= 2*BUFSZ;

    // Pass 4: convert float exponent to int for bit manipulation
    for (unsigned i = 0; i < 2*BUFSZ; i++)
		    chess_prepare_for_pipelining
		    chess_loop_count(2*BUFSZ)
	  {
        aie::vector<int32,8> expint = aie::to_fixed(*pwbufC8++, 16);
        *pwbufB8++ = expint.cast_to<float>();
    }

    pwbufB8 -= 2*BUFSZ;
    pwbufC8 -= 2*BUFSZ;

    // Pass 5: clamp negative values to 0
    for (unsigned i = 0; i < 2*BUFSZ; i++)
		    chess_prepare_for_pipelining
		    chess_loop_count(2*BUFSZ)
	  {
        aie::mask<8> msk_neg = aie::lt(*pwbufB8, 0.0f);
        *pwbufC8++ = aie::select(*pwbufB8++, 0.0f, msk_neg);
    }

    pwbufB8 -= 2*BUFSZ;
    pwbufC8 -= 2*BUFSZ;

    // Pass 6: tree-reduce exp sums using fpadd with shuffle masks
    for (unsigned i = 0; i < BUFSZ; i++)
		    chess_prepare_for_pipelining
		    chess_loop_count(BUFSZ)
	  {
        aie::vector<float,8> expsum;
        auto expvec1 = *pwbufC8++;
        auto expvec2 = *pwbufC8++;
        auto vsum_L1_1 = fpadd(expvec1,expvec1,0,0x67452301);
        auto vsum_L1_2 = fpadd(expvec2,expvec2,0,0x67452301);
        auto vsum_L1_3 = fpadd(expvec1,expvec2,0,0x76543210);
        auto vsum_L2_1 = fpadd(vsum_L1_1,vsum_L1_1,0,0x54761032);
        auto vsum_L2_2 = fpadd(vsum_L1_2,vsum_L1_2,0,0x54761032);
        auto vsum_L2_3 = fpadd(vsum_L1_3,vsum_L1_3,0,0x67452301);
        auto vsum_L2_4 = fpadd(vsum_L1_3,vsum_L1_3,0,0x54761032);
        aie::vector<float,8> vsum_L3_1 = fpadd(vsum_L2_1,vsum_L2_1,0,0x32107654);
        aie::vector<float,8> vsum_L3_2 = fpadd(vsum_L2_2,vsum_L2_2,0,0x32107654);
        aie::vector<float,8> vsum_L3_3 = fpadd(vsum_L2_3,vsum_L2_3,0,0x54761032);
        aie::vector<float,8> vsum_L3_4 = fpadd(vsum_L2_3,vsum_L2_3,0,0x32107654);
        aie::vector<float,8> vsum_L3_5 = fpadd(vsum_L2_4,vsum_L2_4,0,0x32107654);
        expsum[0] = vsum_L3_1[0];
        expsum[4] = vsum_L3_2[0];
        expsum[1] = vsum_L3_3[0];
        expsum[5] = vsum_L3_3[4];
        expsum[2] = vsum_L3_4[0];
        expsum[6] = vsum_L3_4[2];
        expsum[3] = vsum_L3_5[0];
        expsum[7] = vsum_L3_5[1];
        *pwbufB8++ = expsum;
    }

    pwbufB8 -= BUFSZ;

    auto pwbufC4 = aie::begin_restrict_vector<4>(wbufC);

    // Pass 7: compute LLRs = numerator / denominator via inv()
    for (unsigned i = 0; i < BUFSZ; i++)
		    chess_prepare_for_pipelining
		    chess_loop_count(BUFSZ)
	  {
        aie::vector<float,8> expsum = *pwbufB8++;
        *pwbufC4++ = aie::mul(expsum.extract<4>(0),inv(expsum.extract<4>(1)));
    }

    pwbufC4 -= BUFSZ;

    auto pOut = aie::begin_vector<4>(out);

    // Pass 8: write output
    for (unsigned i = 0; i < BUFSZ; i++)
		    chess_prepare_for_pipelining
		    chess_loop_count(BUFSZ)
	  {
		*pOut++ = *pwbufC4++;
	  }
}
