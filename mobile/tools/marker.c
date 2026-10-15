/* Independent BFCodec symbol/state namespace for installed game textures and
 * infov3 metadata. The Python caller compiles a separate shared object per key.
 * Format constants are referenced in docs/mobile-full-assets.md. No third-party
 * decoder implementation is included here: reuse this repository's codec.
 */
#define jbf_setkey marker_setkey
#define jbf_decrypt marker_decrypt
#include "jbf.c"
