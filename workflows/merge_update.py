"""Build a full latest-version view by overlaying inspected update files.
Hard links share immutable payloads; changed JSON is replaced atomically.
"""
from pathlib import Path
import os,json,collections,shutil,hashlib
BASE=PATCH=OUT=None
def clone(src,dst,overwrite=False):
 if not src.exists():return
 for p in src.rglob('*'):
  rel=p.relative_to(src);q=dst/rel
  if p.is_symlink():raise ValueError('Unexpected symlink '+str(p))
  if p.is_dir():q.mkdir(parents=True,exist_ok=True);continue
  q.parent.mkdir(parents=True,exist_ok=True)
  if q.exists():
   if not overwrite:continue
   q.unlink()
  os.link(p,q)
def js(p,obj):
 p.parent.mkdir(parents=True,exist_ok=True);t=p.with_name(p.name+'.tmp');t.write_text(json.dumps(obj,ensure_ascii=False,indent=2));t.replace(p)
def summarize_extras():
 # Read the actual overlaid indexes so replaced containers are counted once.
 textures=[];texture_errors=[];banks=[];bank_errors=[]
 for p in (OUT/'extras/textures').rglob('texture_manifest.json'):
  m=json.loads(p.read_text());errors=m.get('errors',[])
  textures.append({'manifest':str(p.relative_to(OUT)),'source_in_original_version':m.get('source'),'decoded':sum(r.get('status')=='decoded' for r in m.get('records',[])),'errors':len(errors)})
  texture_errors.extend({'manifest':str(p.relative_to(OUT)),'detail':e} for e in errors)
 for p in (OUT/'extras/soundbanks').rglob('va3_manifest.json'):
  m=json.loads(p.read_text());errors=m.get('errors',[])
  banks.append({'manifest':str(p.relative_to(OUT)),'source_in_original_version':m.get('source'),'count':m.get('count',0),'decoded':sum(r.get('status')=='decoded' for r in m.get('records',[])),'errors':len(errors)})
  bank_errors.extend({'manifest':str(p.relative_to(OUT)),'detail':e} for e in errors)
 js(OUT/'texture_stats.json',{'version':OUT.name,'scope':'actual merged containers; replaced base containers excluded','texbin_containers':len(textures),'decoded_texture_images':sum(r['decoded'] for r in textures),'containers':textures,'errors':texture_errors})
 js(OUT/'soundbank_stats.json',{'banks':banks,'decoded_sounds':sum(r['decoded'] for r in banks),'errors':bank_errors})
 individual=[]
 for p in (OUT/'extras/individual_textures').rglob('*.json'):
  if p.with_suffix('.png').exists():individual.append({'manifest':str(p.relative_to(OUT)),'output':str(p.with_suffix('.png').relative_to(OUT))})
 source_errors=[]
 for v in [BASE,PATCH]:
  for n in ['texture_stats.json','soundbank_stats.json','individual_texture_stats.json']:
   q=v/n
   if q.exists():source_errors.extend({'version':v.name,'stage':n,'detail':e} for e in json.loads(q.read_text()).get('errors',[]))
 js(OUT/'individual_texture_stats.json',{'textures_decoded':len(individual),'records':individual,'errors':[]})
 js(OUT/'extras/merge_source_diagnostics.json',{'source_errors':source_errors,'note':'Original-layer errors retained separately; merged totals read actual destination indexes.'})
def main():
 for v in [BASE,PATCH]:
  for n in ['stats.json','texture_stats.json','soundbank_stats.json']:
   if not (v/n).exists():raise ValueError('Not ready: '+str(v/n))
 OUT.mkdir(parents=True,exist_ok=True)
 basedata=find_data(BASE);patchdata=find_data(PATCH)
 baserel=basedata.relative_to(BASE/'raw');patchrel=patchdata.relative_to(PATCH/'raw')
 clone(basedata,OUT/'raw/contents/data')
 clone(patchdata,OUT/'raw/contents/data',True)
 changed={p.parent.name for p in (PATCH/'songs').glob('*/info.json')}
 # A dummy in the update replaces the whole previous song container.
 for p in (BASE/'songs').iterdir():
  if p.is_dir() and p.name not in changed:clone(p,OUT/'songs'/p.name)
 for p in (PATCH/'songs').iterdir():
  if not p.is_dir():continue
  dst=OUT/'songs'/p.name
  if p.name in changed and dst.exists():shutil.rmtree(dst)
  clone(p,dst,True)
  if p.name in changed:
   # Artwork is independent of the song IFS, so unchanged base artwork remains applicable.
   for n in ['cover.png','cover_small.png','cover_sources.json']:
    src=BASE/'songs'/p.name/n
    if src.exists() and not (dst/n).exists():os.link(src,dst/n)
 for category in ['textures','soundbanks','metadata','ifs','individual_textures']:
  clone(BASE/'extras'/category/baserel,OUT/'extras'/category/'contents/data')
  # Any updated source replaces old decoded derivatives, including sentinels
  # that deliberately have no newly decoded manifest or image.
  suffixes={'textures':'.bin','soundbanks':'.va3','ifs':'.ifs','individual_textures':'.tex'}
  if category in suffixes:
   for raw_update in patchdata.rglob('*'+suffixes[category]):
    old=OUT/'extras'/category/'contents/data'/raw_update.relative_to(patchdata).with_suffix('')
    if category=='individual_textures':
     for suffix in ['.png','.json']:
      (OUT/'extras'/category/'contents/data'/raw_update.relative_to(patchdata).with_suffix(suffix)).unlink(missing_ok=True)
    elif old.is_dir():shutil.rmtree(old)
  for d in [PATCH/'extras'/category/patchrel]:
   if not d.is_dir():continue
   target=OUT/'extras'/category/'contents/data'
   marker_names={'textures':'texture_manifest.json','soundbanks':'va3_manifest.json','ifs':'extraction-complete.json'}
   if category in marker_names:
    for marker in d.rglob(marker_names[category]):
     old=target/marker.parent.relative_to(d)
     if old.is_dir():shutil.rmtree(old)
   clone(d,target,True)
 summarize_extras()
 base_meta=json.loads((BASE/'music_metadata.json').read_text());patch_meta=json.loads((PATCH/'music_metadata.json').read_text())
 for cover_index in (OUT/'songs').glob('*/cover_sources.json'):
  sources=json.loads(cover_index.read_text())
  for item in sources:
   if '/data/' in item.get('texture',''):
    item['texture_in_original_version']=item['texture']
    item['texture']='contents/data/'+item['texture'].split('/data/',1)[1]
   if '/data/' in item.get('candidate_png',''):
    item['candidate_png_in_original_version']=item['candidate_png']
    item['candidate_png']='extras/textures/contents/data/'+item['candidate_png'].split('/data/',1)[1]
  available=[];replaced=[]
  for item in sources:
   candidate=item.get('candidate_png')
   if not candidate:
    # Historical indexes used the unsanitized internal name; current names are ASCII identifiers.
    import re
    safe=re.sub(r'[^A-Za-z0-9_.+\- ]','_',item['name']).strip() or 'unnamed'
    candidate=str(Path('extras/textures')/Path(item['texture']).with_suffix('')/(safe+'.png'))
    item['candidate_png']=candidate
   (available if (OUT/candidate).is_file() else replaced).append(item)
  for filename in ['cover.png','cover_small.png']:
   options=[item for item in available if item.get('file')==filename];target=cover_index.parent/filename
   if not options:
    target.unlink(missing_ok=True);continue
   selected=max(options,key=lambda item:(item.get('candidate_width',item.get('width',0))*item.get('candidate_height',item.get('height',0))))
   target.unlink(missing_ok=True);os.link(OUT/selected['candidate_png'],target)
  if replaced:js(cover_index.with_name('cover_sources.replaced_by_update.json'),replaced)
  js(cover_index,available)
 meta={**base_meta,**patch_meta};js(OUT/'music_metadata.json',meta)
 js(OUT/'music_metadata.base.json',base_meta);js(OUT/'music_metadata.update.json',patch_meta)
 rows=[]
 for p in sorted((OUT/'songs').glob('*/info.json')):
  row=json.loads(p.read_text());mid=row['id'];row['metadata']=meta.get(mid,row.get('metadata',{}));row['current_catalogued']=mid in patch_meta
  if mid in base_meta:row['base_metadata']=base_meta[mid]
  if mid in patch_meta:row['current_catalog_metadata']=patch_meta[mid]
  row['merge_source_version']=PATCH.name if mid in changed else BASE.name
  row.setdefault('source_in_original_version',row['source']);source=row['source']
  if '/data/' in source:source='raw/contents/data/'+source.split('/data/',1)[1]
  row['source']=source
  js(p,row);rows.append(row)
 stats={'version':OUT.name,'ifs_song_containers':len(rows),'metadata_entries':len(patch_meta),'preserved_metadata_entries':len(meta),'song_directories':len(rows),'playable_songs':sum(any(c.get('playable') for c in r['charts'].values()) for r in rows),'placeholder_containers':sum(r.get('placeholder',False) for r in rows),'chart_files':sum(len(r['charts']) for r in rows),'playable_charts':sum(c.get('playable',False) for r in rows for c in r['charts'].values()),'mc_files':sum(c.get('mc_exported',False) for r in rows for c in r['charts'].values()),'bgm_decoded':sum('bgm' in r['audio'] for r in rows),'preview_decoded':sum('preview' in r['audio'] for r in rows),'errors':sum(len(r['errors']) for r in rows),'finished':True,'base':BASE.name,'patch':PATCH.name,'changed_song_containers':len(changed),'link_policy':'immutable payloads hard-linked; JSON rewritten atomically; update dummy containers replace old playable resources'}
 stats['merge_assets_note']='Decoded extras hardlinked from base and patch in normalized paths; source manifests retain original provenance.'
 js(OUT/'manifest.json',rows);js(OUT/'stats.json',stats);js(OUT/'errors.json',[{'id':r['id'],**e} for r in rows for e in r['errors']]);js(OUT/'merge_provenance.json',{'base':BASE.name,'patch':PATCH.name,'patched_ifs_ids':sorted(changed),'raw_merge':'Base data tree with patch data file overlay; no original archive modified. No game execution.'})
 print(json.dumps(stats),flush=True)
def find_data(version):
 candidates=[version/'raw/contents/data',version/'raw/data']
 candidates += list((version/'raw').glob('*/contents/data'))+list((version/'raw').glob('*/data'))
 choices=list(dict.fromkeys(p for p in candidates if p.is_dir()))
 if len(choices)!=1:raise ValueError('Expected exactly one data root under raw (contents/data, data, or package/contents/data)')
 return choices[0]
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--base',type=Path,required=True);p.add_argument('--patch',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
 a=p.parse_args();BASE=a.base.resolve();PATCH=a.patch.resolve();OUT=a.out.resolve()
 if OUT==BASE or OUT==PATCH or BASE in OUT.parents or PATCH in OUT.parents:p.error('Output must be separate from both source exports')
 if OUT.exists():p.error('Use a new output directory')
 main()
