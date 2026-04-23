/*
SOURCE: enyac-group/MaxEVA, branch main
PATH: Pattern1_int8/src/kernels.h
DOMAIN: AIE Source
INTERFACE: Window
KEY INTRINSICS: Unknown
VECTOR TYPES: Unknown
*/


#ifndef FUNCTION_KERNELS_H
#define FUNCTION_KERNELS_H

	void opt_blocked_matrix_mult(input_window_int8 * __restrict matA, input_window_int8 * __restrict matB,
						output_window_int32 * __restrict matC);

	void vectorized_add(input_window_int32 * __restrict in_1, input_window_int32 * __restrict in_2,
							output_window_int32 * __restrict out);


#endif
