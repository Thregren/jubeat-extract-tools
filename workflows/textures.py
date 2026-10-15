from pathlib import Path
import sys,json,re,os,collections,hashlib
from assets.texbin import extract_texbin

def run(version):
 files=list((version/'raw').rglob('*.bin'));rows=[];errors=[];magic=collections.Counter();covers=0;selected_hashes={};cover_indexes={}
 for n,p in enumerate(files,1):
  try:
   with p.open('rb') as f:head=f.read(4)
   magic[head.hex()]+=1
   if head not in [b'PXET',b'TEXP']:continue
   rel=p.relative_to(version/'raw');dest=version/'extras/textures'/rel.with_suffix('')
   marker=dest/'texture_manifest.json'
   if marker.exists():result=json.loads(marker.read_text())
   else:result=extract_texbin(p,dest,keep_tdxt=True)
   rows.append({'source':str(rel),'manifest':str(marker.relative_to(version)),'decoded':sum(r['status']=='decoded' for r in result['records']),'errors':len(result['errors'])})
   errors.extend({'source':str(rel),**x} for x in result['errors'])
   for r in result['records']:
    if r['status']!='decoded':continue
    name=r['name'].upper();match=re.search(r'BNR_(BIG_)?ID(\d{8})',name)
    if not match:continue
    mid=match[2]
    layer='mod_'+p.parts[p.parts.index('data_mods')+1] if 'data_mods' in p.parts else 'official'
    song=version/'songs'/mid if layer=='official' else version/'mods'/layer/'songs'/mid
    # Cover-only songs are retained too, with provenance instead of invented charts/audio.
    song.mkdir(parents=True,exist_ok=True)
    target=song/('cover.png' if match[1] else 'cover_small.png')
    src=dest/r['output']
    if not target.exists():os.link(src,target);covers+=1
    links=song/'cover_sources.json'
    if links not in cover_indexes:cover_indexes[links]=json.loads(links.read_text()) if links.exists() else []
    data=cover_indexes[links]
    target_key=str(target)
    if target_key not in selected_hashes:selected_hashes[target_key]=hashlib.sha256(target.read_bytes()).hexdigest()
    selected_sha=selected_hashes[target_key]
    item={'texture':str(rel),'name':r['name'],'file':target.name,'candidate_png':str(src.relative_to(version)),'candidate_sha256':r.get('sha256'),'linked_sha256':selected_sha,'selected':r.get('sha256')==selected_sha,'width':r['width'],'height':r['height']}
    if item not in data:data.append(item)
  except Exception as e:errors.append({'source':str(p),'error':str(e)})
  if n%1000==0:print(version.name,'texture scan',n,'/',len(files),flush=True)
 for links,data in cover_indexes.items():
  tmp=links.with_suffix('.json.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2));tmp.replace(links)
 summary={'version':version.name,'binary_files_scanned':len(files),'binary_magic_counts':dict(magic),'texbin_containers':len(rows),'decoded_texture_images':sum(r['decoded'] for r in rows),'new_cover_links':covers,'errors':errors,'containers':rows}
 (version/'texture_stats.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
 print(version.name,'TEX DONE',len(rows),'containers',summary['decoded_texture_images'],'images','errors',len(errors),flush=True)
 return summary
if __name__=='__main__':
 from ._common import configure
 ROOT,names=configure('TEXP/PXET 与封面导出')
 failed=False
 for name in names:failed|=bool(run(ROOT/name)['errors'])
 raise SystemExit(2 if failed else 0)
