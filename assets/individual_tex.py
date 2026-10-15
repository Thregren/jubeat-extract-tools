"""Standalone single texture decoder. Recognizes verified GFGLTI2D and TDXT signatures.
Original byte row order is preserved. Raises on unverified GL enums or mismatched sizes.
"""
from pathlib import Path
import struct,sys,hashlib
from PIL import Image
from .vendor.tdxt import TDXT

def decode_individual(data):
 if data.startswith(b'GFGLTI2D20140326'):
  if len(data)<52:raise ValueError('short GFGLTI2D header')
  target,level,internal,width,height,border,fmt,datatype,size=struct.unpack_from('<9I',data,16)
  modes={6408:('RGBA',4),6407:('RGB',3),6406:('L',1)}
  if datatype!=5121 or fmt not in modes or level!=0 or border!=0 or target not in (3553,34037):raise ValueError('unsupported GFGLTI2D GL enums '+repr((target,level,internal,border,fmt,datatype)))
  mode,n=modes[fmt]
  if not(0<width<=16384 and 0<height<=16384) or size!=width*height*n or len(data)!=52+size:raise ValueError('GFGLTI2D size/dimensions mismatch')
  image=Image.frombytes(mode,(width,height),data[52:]);meta={'width':width,'height':height,'format':'GFGLTI2D/'+mode,'gl_target':target,'gl_internal_format':internal,'orientation':'source row order preserved'}
 elif data[:4] in (b'TDXT',b'TXDT'):
  tex=TDXT.fromBytes(data)
  if tex.img is None:raise ValueError('unsupported TDXT format '+str(tex.fmt))
  image=tex.img;meta={'width':tex.width,'height':tex.height,'format':tex.fmt}
 else:raise ValueError('unrecognized single texture signature '+repr(data[:16]))
 meta['source_sha256']=hashlib.sha256(data).hexdigest();return image,meta

def extract_individual_tex(source,outpng):
 source=Path(source);outpng=Path(outpng)
 if outpng.exists() or outpng.is_symlink():raise FileExistsError(str(outpng))
 image,meta=decode_individual(source.read_bytes());outpng.parent.mkdir(exist_ok=True,parents=True);image.save(outpng);return {'source':str(source),'output':str(outpng),'status':'decoded',**meta}
if __name__=='__main__':
 import json
 print(json.dumps(extract_individual_tex(sys.argv[1],sys.argv[2]),ensure_ascii=False))
