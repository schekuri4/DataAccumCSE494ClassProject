/*
SOURCE: advent-lab/GAMA, branch main
PATH: aie/aie_constr_buf_array_scaling/aie_MKN_64x184x64_MMUL_4x8x8_i8_i16/aie_top_all.cpp
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
