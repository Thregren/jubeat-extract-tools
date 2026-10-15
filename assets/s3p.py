"""T44 S3P0 -> original S3V0 members and ASF payloads; optional FLAC."""
from pathlib import Path
import hashlib,json,struct,subprocess,shutil
from ._io import new_directory

def extract_s3p(source,output,*,decode=False):
    source=Path(source);out=Path(output);b=source.read_bytes()
    if len(b)<8 or b[:4]!=b'S3P0':raise ValueError('not S3P0')
    count=struct.unpack_from('<I',b,4)[0]
    if count>10000 or 8+8*count>len(b):raise ValueError('S3P table bounds')
    if decode and not shutil.which('ffmpeg'):raise ValueError('FLAC decode requires ffmpeg')
    out=new_directory(out);records=[];errors=[]
    for i in range(count):
        row={'index':i,'status':'failed'}
        try:
            off,size=struct.unpack_from('<II',b,8+8*i)
            if off<8+8*count or off+size>len(b):raise ValueError('S3P member bounds')
            member=b[off:off+size]
            if len(member)<12 or member[:4]!=b'S3V0':raise ValueError('not wrapped S3V0')
            header,payload=struct.unpack_from('<II',member,4)
            if header<12 or header+payload>len(member):raise ValueError('S3V payload bounds')
            path=out/f'{i:04d}.asf';wrapped=out/f'{i:04d}.s3v'
            for p,d in [(path,member[header:header+payload]),(wrapped,member)]:
                if p.exists():raise FileExistsError(str(p))
                p.write_bytes(d)
            row={'index':i,'offset':off,'bytes':size,'source_sha256':hashlib.sha256(member).hexdigest(),'wrapped':wrapped.name,'asf':path.name,'status':'extracted'}
            if decode:
                flac=path.with_suffix('.flac')
                p=subprocess.run(['ffmpeg','-nostdin','-v','warning','-n','-i',str(path),'-map','0:a:0','-c:a','flac',str(flac)],capture_output=True)
                if p.returncode:
                    row['status']='decode_failed'
                    raise ValueError(p.stderr.decode(errors='replace'))
                row.update(flac=flac.name,warnings=p.stderr.decode(errors='replace'),status='decoded')
        except Exception as e:
            row['error']=str(e);errors.append({'index':i,'error':str(e)})
        records.append(row)
    result={'source':str(source),'source_sha256':hashlib.sha256(b).hexdigest(),'count':count,'records':records,'errors':errors}
    (out/'s3p_manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result
