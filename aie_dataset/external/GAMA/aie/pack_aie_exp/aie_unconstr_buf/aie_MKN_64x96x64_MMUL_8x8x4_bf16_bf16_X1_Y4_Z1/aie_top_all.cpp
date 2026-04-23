/*
SOURCE: advent-lab/GAMA, branch main
PATH: aie/pack_aie_exp/aie_unconstr_buf/aie_MKN_64x96x64_MMUL_8x8x4_bf16_bf16_X1_Y4_Z1/aie_top_all.cpp
DOMAIN: AIE Source
INTERFACE: Unknown
KEY INTRINSICS: Unknown
VECTOR TYPES: Unknown
*/


#include <adf.h>
#include "kernels.h"
#include "project.h"

using namespace adf;

simpleGraph mygraph;
#if defined(__AIESIM__) || defined(__X86SIM__)
int main(void) {
  mygraph.init();
  mygraph.run(4);
  mygraph.end();
  return 0;
}
#endif
