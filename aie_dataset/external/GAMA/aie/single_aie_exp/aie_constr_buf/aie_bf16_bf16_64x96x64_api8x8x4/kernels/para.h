/*
SOURCE: advent-lab/GAMA, branch main
PATH: aie/single_aie_exp/aie_constr_buf/aie_bf16_bf16_64x96x64_api8x8x4/kernels/para.h
DOMAIN: AIE Source
INTERFACE: Window
KEY INTRINSICS: Unknown
VECTOR TYPES: Unknown
*/

#ifndef PARA_H
#define PARA_H
#include <adf/stream/types.h>
#include <adf.h>
#include <aie_api/aie.hpp>
const int L0_h1 = 64;
const int L0_w1 = 96;
const int L0_w2 = 64;

using namespace adf;

void mm_kernel0(input_buffer<bfloat16> &__restrict matA,
                input_buffer<bfloat16> &__restrict matB,
                output_buffer<bfloat16> &__restrict matC);

#endif