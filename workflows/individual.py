from pathlib import Path
import sys,json,os
from assets.individual_tex import extract_individual_tex

def main():
 global ROOT
 from ._common import configure
 ROOT,names=configure('本地资源批处理')
 failed=False
 for name in names:
  v=ROOT/name;rows=[];errors=[]
  for p in (v/'raw').rglob('*.tex'):
   rel=p.relative_to(v/'raw');dest=v/'extras/individual_textures'/rel.with_suffix('.png');marker=dest.with_suffix('.json')
   try:
    if marker.exists():r=json.loads(marker.read_text())
    else:
     dest.parent.mkdir(parents=True,exist_ok=True);r=extract_individual_tex(p,dest);marker.write_text(json.dumps(r,ensure_ascii=False,indent=2))
    rows.append({'source':str(rel),'output':str(dest.relative_to(v)),'meta':r})
   except Exception as e:errors.append({'source':str(rel),'error':str(e)})
  (v/'individual_texture_stats.json').write_text(json.dumps({'textures_decoded':len(rows),'records':rows,'errors':errors},ensure_ascii=False,indent=2))
  print(name,'individual TEX',len(rows),'errors',len(errors),flush=True)
  failed|=bool(errors)
 return 2 if failed else 0
if __name__=='__main__':raise SystemExit(main())
