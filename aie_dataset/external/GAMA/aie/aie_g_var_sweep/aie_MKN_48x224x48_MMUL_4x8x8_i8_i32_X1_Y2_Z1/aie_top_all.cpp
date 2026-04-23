/*
SOURCE: advent-lab/GAMA, branch main
PATH: aie/aie_g_var_sweep/aie_MKN_48x224x48_MMUL_4x8x8_i8_i32_X1_Y2_Z1/aie_top_all.cpp
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
