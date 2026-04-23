/*
SOURCE: rehohoho/onnx2versal, branch master
PATH: design/aie_src/identity.h
DOMAIN: AIE Source
INTERFACE: Stream
KEY INTRINSICS: Unknown
VECTOR TYPES: Unknown
*/

#ifndef IDENTITY_H_
#define IDENTITY_H_

#include <adf.h>

/** 
 * @defgroup IdentityKernels
 * @ingroup Identity
 * 
 * @{
 */

/**
 * @brief Scalar implementation,
 * Identity<8> total = 35
 */
template <typename TT, int N>
class Identity {
  public:
    void filter(
      input_stream<TT>* in,
      output_stream<TT>* out
    );
    static void registerKernelClass() {
      REGISTER_FUNCTION(Identity::filter);
    }
};
/** @}*/


#endif // IDENTITY_H_
