/* Independently written implementation of the observed PKFS rolling XOR.
 * No third-party BMS source or game binary code is included. */
#include <stdint.h>
#include <stddef.h>
static uint32_t rol11(uint32_t x) { return (x << 11) | (x >> 21); }
void legacy_pkfs_xor(uint8_t *data, size_t length, uint32_t crc,
                     uint16_t subkey, int historical_tail) {
    uint32_t key = rol11(crc + (uint32_t)subkey);
    size_t pos = 0;
    for (; length - pos >= 4; pos += 4) {
        for (unsigned j = 0; j < 4; ++j)
            data[pos + j] ^= (uint8_t)(key >> (j * 8));
        key = rol11(key + (uint32_t)subkey);
    }
    for (unsigned j = 0; j < length - pos; ++j)
        data[pos + (historical_tail ? 0 : j)] ^= (uint8_t)(key >> (j * 8));
}
