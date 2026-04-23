/*
SOURCE: Xilinx/Vitis-Tutorials, branch 2024.2
PATH: AI_Engine_Development/AIE/Feature_Tutorials/03-rtp-reconfiguration/async_array_rtp/aie/graph.cpp
DOMAIN: Runtime Parameter Reconfiguration
INTERFACE: Unknown
KEY INTRINSICS: Unknown
VECTOR TYPES: Unknown
*/

/*
Copyright (C) 2023, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
*/
#include <adf.h>
#include "graph.h"
#include <unistd.h>

using namespace adf;

rtpgraph gr;

#if defined(__AIESIM__) || defined(__X86SIM__)
int main(int argc, char ** argv) { 
	int value[256];
	for(int i=0;i<256;i++){
		value[i]=i;
	}
	gr.init();
	gr.run(4);

	gr.update(gr.value,value,256);

	gr.end();
	return 0;
};
#endif
