"""Fast lossless reconstruction of Konami BMP OKI4S into PCM/WAV.
Algorithm matches jubeat-extract-tools/arcade/import_ifs.py, with bounds checks.
"""
from pathlib import Path
import ctypes,struct,wave,io
from ._native import load_library
try:
 _LIB=load_library('oki4s.c')
 _LIB.decode_oki4s.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_size_t,ctypes.c_int,ctypes.c_void_p]
 _LIB.decode_oki4s.restype=ctypes.c_int
except (OSError, RuntimeError):
 _LIB=None
def decode_bmp(data):
 if len(data)<32 or data[:4]!=b'BMP\0':raise ValueError('Not Konami BMP audio')
 count,loop_start,loop_end=struct.unpack_from('>III',data,4);ch=data[16] or struct.unpack_from('<H',data,16)[0];rate=struct.unpack_from('>I',data,20)[0]
 if ch not in (1,2) or not 1000<=rate<=384000:raise ValueError('Invalid BMP channels/rate')
 adpcm=data[32:];limit=len(adpcm)*(2 if ch==1 else 1)
 declared=count
 if count==0 or count>limit:count=limit
 if not count:raise ValueError('Empty BMP')
 if _LIB is None:
  from arcade.import_ifs import decode_bmp_to_pcm
  pcm,_,_=decode_bmp_to_pcm(data)
 else:
  pcm=ctypes.create_string_buffer(count*ch*2);inp=ctypes.create_string_buffer(adpcm)
  ret=_LIB.decode_oki4s(inp,len(adpcm),count,ch,pcm)
  if ret:raise ValueError('OKI4S decode error '+str(ret))
  pcm=pcm.raw
 return pcm,{'frames':count,'declared_frames':declared,'sample_rate':rate,'channels':ch,'loop_start':loop_start,'loop_end':loop_end,'duration_seconds':count/rate,'header_count_clamped':declared!=count}
def bmp_to_wav(data,path):
 path=Path(path)
 if path.exists() or path.is_symlink():raise FileExistsError(str(path))
 pcm,meta=decode_bmp(data);path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_suffix(path.suffix+'.tmp')
 with wave.open(str(tmp),'wb') as w:w.setnchannels(meta['channels']);w.setsampwidth(2);w.setframerate(meta['sample_rate']);w.writeframes(pcm)
 tmp.replace(path);return meta
