/*
SOURCE: donghoang93/Xilinx-Vitis-Tutorials, branch 2022.1
PATH: AI_Engine_Development/Feature_Tutorials/03-rtp-reconfiguration/step2/aie/kernels/dds.cc
DOMAIN: Runtime Parameter Reconfiguration
INTERFACE: Window
KEY INTRINSICS: aie::vector_decl_align, aie::vector, aie::load_v, aie::sincos, aie::store_v, window_writeincr
VECTOR TYPES: aie::vector<cint16,8>
*/

/**********
© Copyright 2020-2022 Xilinx, Inc.
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
    http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
**********/
#include "stdlib.h"
#include <stdio.h>
#include <aie_api/aie.hpp>
#include <aie_api/aie_adf.hpp>
#include "aie_api/utils.hpp"

static int32 phase_in;

alignas(aie::vector_decl_align) static int16 dds_stored [16] = {32767, 0, 32767, 0, 32767, 0,  32767, 0, 32767, 0, 32767, 0, 32767, 0, 32767, 0};

void init_dds() {
  aie::vector<cint16,8> dds =  aie::load_v<8>((cint16*)dds_stored);
  phase_in = 0;
  
  auto [sin_,cos_] = aie::sincos(phase_in ); // phase_in + 0
  cint16 scvalues={cos_,sin_};
  
  dds.push(scvalues);
  aie::store_v((cint16*)dds_stored, dds);
}

void sine(const int32 phase_increment,output_window<cint16> * owin) {
  //Initialize vector from memory
  aie::vector<cint16,8> dds=aie::load_v<8>((cint16*)dds_stored);

  for (unsigned k = 0; k<128; k++){
    phase_in += (phase_increment << 6);
    auto [sin_,cos_] = aie::sincos(phase_in << 14) ; // phase_in + (7i + j + 1) * phase_increment
    cint16 scvalues={cos_,sin_};
    dds.push(scvalues);

    window_writeincr(owin, scvalues);
    aie::store_v((cint16*)dds_stored, dds);
  };
};

