from pathlib import Path
import sys,json,hashlib,struct
from assets.texbin import safe

def main():
 global ROOT
 from ._common import configure
 ROOT,names=configure('本地资源批处理')
 failed=False
 for name in names:
  v=ROOT/name;paths=list((v/'songs').glob('*/cover_sources.json'))+list((v/'mods').glob('*/songs/*/cover_sources.json'));errors=[];updated=0
  for p in paths:
   try:
    data=json.loads(p.read_text());hashes={}
    for item in data:
     target=p.parent/item['file']
     if item['file'] not in hashes:
      d=target.read_bytes();dimensions=struct.unpack_from('>II',d,16) if d.startswith(b'\x89PNG') else None
      hashes[item['file']]=(hashlib.sha256(d).hexdigest(),dimensions)
     linked_sha,dim=hashes[item['file']]
     candidate=v/item['candidate_png'] if item.get('candidate_png') else v/'extras/textures'/Path(item['texture']).with_suffix('')/(safe(item['name'])+'.png')
     sha=item.get('candidate_sha256') or item.pop('sha256',None)
     if candidate.exists():
      if not sha:sha=hashlib.sha256(candidate.read_bytes()).hexdigest()
      item['candidate_png']=str(candidate.relative_to(v))
     else:errors.append({'metadata':str(p.relative_to(v)),'missing_candidate':str(candidate)})
     item['candidate_sha256']=sha;item['linked_sha256']=linked_sha;item['selected']=sha==linked_sha
     if 'width' in item:item['candidate_width']=item.pop('width')
     if 'height' in item:item['candidate_height']=item.pop('height')
     if dim:item['linked_width'],item['linked_height']=dim
    q=p.with_suffix('.json.tmp');q.write_text(json.dumps(data,ensure_ascii=False,indent=2));q.replace(p);updated+=1
   except Exception as e:errors.append({'metadata':str(p),'error':str(e)})
  report={'version':name,'cover_source_indexes_audited':updated,'errors':errors}
  (v/'cover_provenance_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(report,flush=True)
  failed|=bool(errors)
 return 2 if failed else 0
if __name__=='__main__':raise SystemExit(main())
