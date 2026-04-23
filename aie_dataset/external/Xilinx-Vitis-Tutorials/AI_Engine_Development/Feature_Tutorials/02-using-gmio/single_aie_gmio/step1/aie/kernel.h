/*
SOURCE: donghoang93/Xilinx-Vitis-Tutorials, branch 2022.1
PATH: AI_Engine_Development/Feature_Tutorials/02-using-gmio/single_aie_gmio/step1/aie/kernel.h
DOMAIN: GMIO / Data Movement
INTERFACE: Window
KEY INTRINSICS: Unknown
VECTOR TYPES: Unknown
*/

/**********
© Copyright 2020 Xilinx, Inc.
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
#ifndef __KERNEL_H__
#define __KERNEL_H__
#include <adf.h>
void weighted_sum_with_margin(input_window<int32> * restrict in, output_window<int32> * restrict out);
#endif
