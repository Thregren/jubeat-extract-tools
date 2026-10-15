#!/usr/bin/env python3
"""Offline Konami TEXP/TEXBIN -> named PNGs, with original TDXT and manifest.

Container and LZSS layout derived from mon/ifs_layeredfs src/texbin.cpp.
Pixel codecs from DragonMinded/bemaniutils tdxt.py+dxt.py (public domain).
"""
PIXEL_DECODE_POLICY = 'texp-0x10-rgba-v1'

from pathlib import Path
import sys,struct,json,re,hashlib,argparse,ctypes
from ._native import load_library
try:
 _native=load_library('lzss.c')
 _native.texbin_lzss.argtypes=[ctypes.c_char_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t]
 _native.texbin_lzss.restype=ctypes.c_int
except (OSError, RuntimeError):
 _native=None
from .vendor.tdxt import TDXT
from PIL import Image
from ._io import new_directory
_original_raw_decoder = TDXT._rawToImg

def _raw_decoder(width,height,fmt,endian,invert,raw):
 # Pillow uses its compiled BCn decoders; real DXT5 sample pixels matched vendor exactly.
 if fmt in (0x16,0x18,0x1a) and endian == "<":
  version={0x16:(1,"DXT1"),0x18:(2,"DXT3"),0x1a:(3,"DXT5")}[fmt]
  return Image.frombytes("RGBA",(width,height),raw,"bcn",version)
 if fmt == 0x06:
  return Image.frombytes("L",(width,height),raw)
 if fmt == 0x0c:
  out=bytearray(width*height*4)
  for i in range(width*height):
   lo=(raw[2*i]&0xc0)|((raw[2*i]&0x3f)>>1)
   v=lo|(raw[2*i+1]<<8)
   out[4*i:4*i+4]=bytes([((((v&0xf800)>>11)*527+23)>>6),((((v&0x7e0)>>5)*259+33)>>6),(((v&31)*527+23)>>6),255])
  return Image.frombytes("RGBA",(width,height),bytes(out))
 if fmt == 0x0d:
  out=bytearray(width*height*4)
  for i in range(width*height):
   v=raw[2*i]|(raw[2*i+1]<<8)
   out[4*i:4*i+4]=bytes([((v>>12)&15)*17,((v>>8)&15)*17,((v>>4)&15)*17,(v&15)*17])
  return Image.frombytes("RGBA",(width,height),bytes(out))
 return _original_raw_decoder(width,height,fmt,endian,invert,raw)
TDXT._rawToImg=staticmethod(_raw_decoder)

def lzss(data):
 if len(data)<8:raise ValueError('short LZSS header')
 size,compressed=struct.unpack_from('>II',data)
 if size>256*1024*1024:raise ValueError('LZSS output over 256 MiB')
 if not compressed:
  if len(data)<size+8:raise ValueError('uncompressed payload truncated')
  return data[8:8+size]
 if len(data)<8+compressed:raise ValueError('compressed payload truncated')
 if _native is not None:
  target=ctypes.create_string_buffer(size)
  status=_native.texbin_lzss(data[8:8+compressed],compressed,target,size)
  if status:raise ValueError(f"native LZSS error {status}")
  return target.raw
 window=bytearray(4096);wi=4078;out=bytearray();i=8;end=8+compressed
 while i<end and len(out)<size:
  flags=data[i];i+=1
  for _ in range(8):
   if len(out)>=size:break
   if flags&1:
    if i>=end:raise ValueError('truncated literal')
    v=data[i];i+=1;out.append(v);window[wi]=v;wi=(wi+1)&4095
   else:
    if i+2>end:raise ValueError('truncated backreference')
    off=data[i]|((data[i+1]&240)<<4);length=(data[i+1]&15)+3;i+=2
    for _ in range(min(length,size-len(out))):
     v=window[off];off=(off+1)&4095;out.append(v);window[wi]=v;wi=(wi+1)&4095
   flags>>=1
 if len(out)!=size:raise ValueError(f'LZSS produced {len(out)} expected {size}')
 return bytes(out)

def safe(name):return re.sub(r'[^A-Za-z0-9_.+\- ]','_',name).strip() or 'unnamed'
def extract_texbin(source,output,*,keep_tdxt=True):
 source=Path(source);output=Path(output);b=source.read_bytes()
 if b[:4] not in [b'PXET',b'TEXP']:raise ValueError('not TEXP/PXET')
 endian='<' if b[:4]==b'PXET' else '>'
 def u32(off):
  if off<0 or off+4>len(b):raise ValueError('u32 out of bounds')
  return struct.unpack_from(endian+'I',b,off)[0]
 def names(off):
  if off<0 or off+28>len(b) or b[off:off+4] not in [b'PMAN',b'NAMP']:raise ValueError('bad PMAN names header')
  count=u32(off+16);head=u32(off+24)
  if count>100000 or off+head+12*count>len(b):raise ValueError('name table out of bounds')
  result={}
  for i in range(count):
   _,idx,ptr=struct.unpack_from(endian+'III',b,off+head+12*i)
   pos=off+ptr;end=b.find(b'\0',pos)
   if pos<0 or end<0 or pos>=len(b):raise ValueError('name string out of bounds')
   result[idx]=b[pos:end].decode('cp932',errors='replace')
  return result
 if len(b)<64:raise ValueError('short TEXP header')
 total=u32(12);count=u32(20);rectoff=u32(32);nameoff=u32(52);entryoff=u32(60)
 if count>100000 or entryoff+12*count>len(b):raise ValueError('data table out of bounds')
 labels=names(nameoff)
 output=new_directory(output);images={};records=[];errors=[]
 for i in range(count):
  name=labels.get(i,f'texture_{i:04d}');rec={'kind':'texture','id':i,'name':name}
  try:
   _,declared_size,off=struct.unpack_from(endian+'III',b,entryoff+12*i)
   if off<0 or off+8>len(b):raise ValueError('texture payload offset')
   decomp,comp=struct.unpack_from('>II',b,off)
   length=8+(comp or decomp)
   if off+length>len(b):raise ValueError('texture payload boundary')
   raw=lzss(b[off:off+length]);tdxt=TDXT.fromBytes(raw)
   # TEXP fmt 0x10 is already RGBA (ifs_layeredfs texbin.cpp happy path).
   # Keep the standalone TDXT/vendor BGRA default and all other formats unchanged.
   image=Image.frombytes('RGBA',(tdxt.width,tdxt.height),tdxt.raw) if tdxt.fmt==0x10 else tdxt.img
   if image is None:raise ValueError(f'unsupported TDXT fmt {tdxt.fmt:#x}')
   images[i]=image;path=output/(safe(name)+'.png')
   image.save(path)
   if keep_tdxt:(output/(safe(name)+'.tdxt')).write_bytes(raw)
   rec.update(output=path.name,width=tdxt.width,height=tdxt.height,format=hex(tdxt.fmt),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),compressed_offset=off,compressed_bytes=comp,decompressed_bytes=decomp,declared_size=declared_size,status='decoded')
  except Exception as e:rec.update(status='failed',error=str(e));errors.append({'name':name,'error':str(e)})
  records.append(rec)
 if rectoff:
  if b[rectoff:rectoff+4] not in [b'TCER',b'RECT']:raise ValueError('bad TCER rectangle header')
  n=u32(rectoff+16);rnames=names(rectoff+u32(rectoff+20));roff=rectoff+u32(rectoff+24)
  for i in range(n):
   name=rnames.get(i,f'rect_{i:04d}');rec={'kind':'rectangle','id':i,'name':name}
   try:
    parent,x1,x2,y1,y2=struct.unpack_from(endian+'IHHHH',b,roff+12*i)
    if parent not in images:raise ValueError('missing parent image')
    image=images[parent]
    if not (0<=x1<x2<=image.width and 0<=y1<y2<=image.height):raise ValueError('rectangle outside parent')
    path=output/(safe(name)+'.png');image.crop((x1,y1,x2,y2)).save(path)
    rec.update(parent=labels.get(parent,parent),rect=[x1,y1,x2,y2],width=x2-x1,height=y2-y1,output=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),status='decoded')
   except Exception as e:rec.update(status='failed',error=str(e));errors.append({'name':name,'error':str(e)})
   records.append(rec)
 result={'pixel_decode_policy':PIXEL_DECODE_POLICY,'source':str(source),'source_sha256':hashlib.sha256(b).hexdigest(),'archive_size_declared':total,'archive_size_actual':len(b),'texture_count':count,'records':records,'errors':errors}
 (output/'texture_manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 return result

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('source');p.add_argument('output');p.add_argument('--no-tdxt',action='store_true');args=p.parse_args()
 result=extract_texbin(args.source,args.output,keep_tdxt=not args.no_tdxt)
 print(json.dumps({'source':args.source,'decoded':sum(r['status']=='decoded' for r in result['records']),'errors':result['errors']},ensure_ascii=False))
 if result['errors']:sys.exit(1)
if __name__=='__main__':main()
