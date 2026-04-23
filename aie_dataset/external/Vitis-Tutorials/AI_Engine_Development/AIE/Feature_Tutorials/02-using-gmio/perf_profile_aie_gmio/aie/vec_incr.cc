/*
SOURCE: Xilinx/Vitis-Tutorials, branch 2024.2
PATH: AI_Engine_Development/AIE/Feature_Tutorials/02-using-gmio/perf_profile_aie_gmio/aie/vec_incr.cc
DOMAIN: GMIO / Data Movement
INTERFACE: Window
KEY INTRINSICS: aie::begin_vector, aie::vector, aie::broadcast, aie::add, aie::tile, aie::begin, tile
VECTOR TYPES: aie::vector<int32,16>
*/

/*
Copyright (C) 2023, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
*/
#include <aie_api/aie.hpp>
#include <aie_api/aie_adf.hpp>
#include <aie_api/utils.hpp>
using namespace adf;
void vec_incr(input_buffer<int32,extents<256>>& __restrict data,output_buffer<int32,extents<258>>& __restrict out){
	auto inIter=aie::begin_vector<16>(data);
	auto outIter=aie::begin_vector<16>(out);
	aie::vector<int32,16> vec1=aie::broadcast<int32>(1);
	for(int i=0;i<16;i++)
	chess_prepare_for_pipelining
	{
		aie::vector<int32,16> vdata=*inIter++;
		aie::vector<int32,16> vresult=aie::add(vdata,vec1);
		*outIter++=vresult;
	}
	aie::tile tile=aie::tile::current();
	unsigned long long time=tile.cycles();//cycle counter of the AI Engine tile
	decltype(aie::begin(out)) p=*(decltype(aie::begin(out))*)&outIter;
	*p++=time&0xffffffff;
	*p++=(time>>32)&0xffffffff;
}
