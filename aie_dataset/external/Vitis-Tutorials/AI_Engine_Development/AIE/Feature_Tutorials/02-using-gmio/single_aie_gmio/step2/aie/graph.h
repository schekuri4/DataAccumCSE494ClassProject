/*
SOURCE: Xilinx/Vitis-Tutorials, branch 2024.2
PATH: AI_Engine_Development/AIE/Feature_Tutorials/02-using-gmio/single_aie_gmio/step2/aie/graph.h
DOMAIN: GMIO / Data Movement
INTERFACE: PLIO/GMIO
KEY INTRINSICS: connect, runtime
VECTOR TYPES: Unknown
*/

/*
Copyright (C) 2023, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
*/
#ifndef __GRAPH_H__
#define __GRAPH_H__

#include <adf.h>
#include "kernel.h"

class mygraph: public adf::graph
{
private:
	adf::kernel k_m;

public:
	adf::output_gmio gmioOut; 
	adf::input_gmio gmioIn;

	mygraph(){
		k_m=adf::kernel::create(vectorized_weighted_sum_with_margin);
		gmioOut=adf::output_gmio::create("gmioOut",64,1000);
		gmioIn=adf::input_gmio::create("gmioIn",64,1000);
		
		adf::connect<>(gmioIn.out[0], k_m.in[0]);
		adf::connect<>(k_m.out[0], gmioOut.in[0]);
		adf::source(k_m) = "weighted_sum.cc";
		adf::runtime<adf::ratio>(k_m)= 0.9;
	};
};

#endif
