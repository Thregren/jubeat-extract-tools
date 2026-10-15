"""Read-only VA3W soundbank export. Table layout verified against fisyher/gitadora-customs/vas3tool.py.
Payload OKI4S step sizes/nibble order match its _misc/adpcmwave.pyx.
Volume and pan are retained in metadata and not applied to PCM samples.
"""
from pathlib import Path
import struct,json,re
from .oki4s import bmp_to_wav
from ._io import new_directory

def extract_va3(source,output):
 source=Path(source);out=Path(output);b=source.read_bytes()
 if len(b)<28 or b[:4]!=b'VA3W':raise ValueError('not VA3W')
 v1,v2,v3,ver,count,gsize,gstart,estart,dstart=struct.unpack_from('<BBBBIIIII',b,4)
 if count>100000 or estart+64*count>len(b) or dstart>len(b):raise ValueError('VA3 table bounds')
 gmagic=b[gstart:gstart+4].decode('ascii','replace')
 if gmagic!='GDXH' or (v1,v2,v3,ver)!=(1,0,0,2):raise ValueError('Unverified VA3 variant: '+repr(((v1,v2,v3,ver),gmagic)))
 out=new_directory(out);records=[];errors=[]
 for i in range(count):
  try:
   off,size,ch,bits,rate,unk1,unk2,vol,pan,sid,flags,extra=struct.unpack_from('<IIHHIIIBBHHH',b,estart+i*64)
   name=b[estart+i*64+32:estart+i*64+64].split(b'\0')[0].decode('cp932','replace');safe=re.sub(r'[^A-Za-z0-9_.+\- ]','_',name) or 'unnamed'
   rec={'entry':i,'name':name,'id':sid,'offset':off,'compressed_bytes':size,'channels':ch,'bits':bits,'sample_rate':rate,'volume':vol,'pan':pan,'flags':flags,'extra':extra,'unknown1':unk1,'unknown2':unk2}
   if ch not in (1,2) or bits!=16:raise ValueError('unsupported VA3 channels/bits')
   if dstart+off+size>len(b):raise ValueError('VA3 payload bounds')
   if size==0:rec['status']='empty';records.append(rec);continue
   payload=b[dstart+off:dstart+off+size];h=bytearray(32);h[:4]=b'BMP\0';struct.pack_into('>I',h,4,size*(2 if ch==1 else 1));h[16]=ch;struct.pack_into('>I',h,20,rate);target=out/(f'{i:04d}_{sid:04x}_{safe}.wav')
   if target.exists():
    import wave
    with wave.open(str(target)) as w:meta={'frames':w.getnframes(),'sample_rate':w.getframerate(),'channels':w.getnchannels()}
   else:meta=bmp_to_wav(bytes(h)+payload,target)
   rec.update(status='decoded',output=target.name,audio=meta);records.append(rec)
  except Exception as e:errors.append({'entry':i,'error':str(e)});records.append({'entry':i,'status':'failed','error':str(e)})
 result={'source':str(source),'version':ver,'gdx_type':gmagic,'count':count,'records':records,'errors':errors,'notes':'Declared byte counts honored. No volume/pan adjustment. ADPCM decoding preserves reconstructed PCM; no lossy re-encode.'}
 (out/'va3_manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));return result
if __name__=='__main__':
 import sys
 r=extract_va3(sys.argv[1],sys.argv[2]);print(json.dumps({'decoded':sum(x['status']=='decoded' for x in r['records']),'errors':r['errors']}))
