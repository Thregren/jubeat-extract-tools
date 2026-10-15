from pathlib import Path
import json,sys,collections

def main():
 global ROOT
 from ._common import configure
 ROOT,names=configure('本地资源批处理')
 for name in names:
  v=ROOT/name;manifest=v/'manifest.json';rows=json.loads(manifest.read_text());by_layer=collections.defaultdict(collections.Counter)
  for r in rows:
   before=dict(r)
   missing=[]
   for role in ['bgm','preview']:
    if role not in r['audio']:missing.append(role)
   for code in ['BSC','ADV','EXT']:
    if not r['charts'].get(code,{}).get('playable'):missing.append(code)
   if r['errors']:status='conversion_error'
   elif not missing:status='complete'
   elif 'bgm' in r['audio'] and len(r['audio'])==1 and not r['charts']:status='bgm_only'
   elif not r['audio'] and not any(c.get('playable') for c in r['charts'].values()):status='raw_only'
   else:status='partial'
   r['resource_status']=status;r['missing_export_components']=missing
   layer=r.get('layer','official');by_layer[layer][status]+=1
   if status=='raw_only':
    dummy=len(r.get('members',[]))==1 and r['members'][0].get('name')=='dummy.dat' and r['members'][0].get('bytes')==1 and r['members'][0].get('sha256') in {'6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d','18ac3e7343f016890c510e93f935261169d9e3f565436429830faf0934f4f8e4'}
    r['raw_only_kind']='verified_single_byte_dummy' if dummy else 'opaque_or_nonplayable'
    by_layer[layer]['dummy_placeholders' if dummy else 'opaque_containers']+=1
   by_layer[layer]['containers']+=1;by_layer[layer]['bgm']+=int('bgm' in r['audio']);by_layer[layer]['preview']+=int('preview' in r['audio']);by_layer[layer]['chart_files']+=len(r['charts']);by_layer[layer]['playable_charts']+=sum(c.get('playable',False) for c in r['charts'].values())
   song=v/'songs'/r['id'] if layer=='official' else v/'mods'/layer/'songs'/r['id']
   if r!=before:
    p=song/'info.json';t=p.with_suffix('.json.tmp');t.write_text(json.dumps(r,ensure_ascii=False,indent=2));t.replace(p)
  temp=manifest.with_suffix('.json.tmp');temp.write_text(json.dumps(rows,ensure_ascii=False,indent=2));temp.replace(manifest)
  stats=json.loads((v/'stats.json').read_text());stats['resource_classification']={k:dict(c) for k,c in by_layer.items()};stats['complete_songs']=sum(c['complete'] for c in by_layer.values());(v/'stats.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2))
  print(name,stats['resource_classification'],flush=True)
if __name__=='__main__':main()
