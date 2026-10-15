#include <stdint.h>
#include <stddef.h>
static const int st[49]={16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,307,337,371,408,449,494,544,598,658,724,796,876,963,1060,1166,1282,1411,1552};
static const int ix[16]={-1,-1,-1,-1,2,4,6,8,-1,-1,-1,-1,2,4,6,8};
int decode_oki4s(const uint8_t *in,size_t n,size_t frames,int ch,int16_t *out){
 if(ch!=1&&ch!=2)return -1;
 if(frames>(ch==1?n*2:n))return -2;
 int hist[2]={0},step[2]={0};
 for(size_t i=0;i<frames;i++)for(int c=0;c<ch;c++){
 int b=in[ch==2?i:i/2];int code=ch==2?(c==0?b>>4:b&15):(i%2==0?b>>4:b&15);
 int delta=st[step[c]]<<4,acc=delta>>3;
 if(code&1)acc+=delta>>2;if(code&2)acc+=delta>>1;if(code&4)acc+=delta;if(code&8)acc=-acc;
 hist[c]+=acc;if(hist[c]>32767)hist[c]=32767;if(hist[c]<-32768)hist[c]=-32768;
 step[c]+=ix[code];if(step[c]<0)step[c]=0;if(step[c]>48)step[c]=48;
 out[i*ch+c]=(int16_t)hist[c];
 }
 return 0;
}
int decode_lzss4078(const uint8_t *in,size_t n,uint8_t *out,size_t size){
 uint8_t window[4096]={0};size_t wi=4078,i=0,o=0;
 while(i<n&&o<size){unsigned flags=in[i++];for(int bit=0;bit<8&&o<size;bit++,flags>>=1){
  if(flags&1){if(i>=n)return -1;out[o++]=window[wi]=in[i++];wi=(wi+1)&4095;}
  else {if(i+2>n)return -2;unsigned off=in[i]|((in[i+1]&240)<<4),len=(in[i+1]&15)+3;i+=2;
   while(len--&&o<size){out[o++]=window[wi]=window[off];wi=(wi+1)&4095;off=(off+1)&4095;}}
 }}return o==size?0:-3;
}
