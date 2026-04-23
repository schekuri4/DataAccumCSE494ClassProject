/*
SOURCE: advent-lab/GAMA, branch main
PATH: aie/pack_aie_exp/aie_conf_buf/aie_MKN_48x240x48_MMUL_4x8x8_i8_i32_Y1_G4_X1/aie_top_all.cpp
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
