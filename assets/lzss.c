#include <stddef.h>
#include <stdint.h>
int texbin_lzss(const uint8_t *in, size_t insz, uint8_t *out, size_t outsz) {
 uint8_t window[4096]={0};size_t ip=0,op=0;unsigned wp=4078;
 while(ip<insz && op<outsz) {
  unsigned flags=in[ip++];
  for(unsigned bit=0;bit<8 && op<outsz;bit++,flags>>=1) {
   if(flags&1) {
    if(ip>=insz)return -1;
    uint8_t v=in[ip++];out[op++]=window[wp]=v;wp=(wp+1)&4095;
   } else {
    if(ip+2>insz)return -2;
    unsigned pos=in[ip]|((in[ip+1]&0xf0)<<4),len=(in[ip+1]&15)+3;ip+=2;
    while(len-- && op<outsz) {
     uint8_t v=window[pos];pos=(pos+1)&4095;out[op++]=window[wp]=v;wp=(wp+1)&4095;
    }
   }
  }
 }
 return op==outsz?0:-3;
}
