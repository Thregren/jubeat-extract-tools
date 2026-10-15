#!/usr/bin/env python3
"""Choose real exported version-specific examples using completed JSON indices only.
No raw-directory walk; no PNG/WAV content is read. Existing successful export
records and concrete output-file existence are required for every example.
"""
from pathlib import Path,PurePosixPath
import argparse,collections,hashlib,json,re,sys
ROOT=Path(__file__).resolve().parents[1]
VERSIONS=['11_festo_2022052400','12_Ave_2023080800','13_beyond_the_Ave_2025020500','14_beyond_the_Ave_2026072200_update']
HINTS={'11':['l44fo'],'12':['l44av'],'13':['l44avby','l44av'],'14':['l44avby','l44av']}
TOKENS=['title','logo','light_chat','travel','hike','dungeon','torokko','team_battle','jubility','street','jubibell','jukebox','navi','recommend','game_over','result','matching','entry','demo','play','select','menu','theme']
PRIORITY={s:100-i for i,s in enumerate(TOKENS)}

def read(path):
 if path.stat().st_size>128*1024*1024:raise ValueError('index exceeds bounded JSON budget')
 return json.loads(path.read_text(encoding='utf-8-sig'))
def dump(path,obj):
 temp=path.with_name(path.name+'.tmp');temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n');temp.replace(path)
def layer(source):
 p=PurePosixPath(str(source)).parts
 return 'mod_'+p[p.index('data_mods')+1] if 'data_mods' in p and p.index('data_mods')+1<len(p) else 'official'
def prefixes(source):return re.findall(r'(?<![a-z0-9])(l44[a-z]{2,6})(?=_)',str(source).lower())
def categories(source):return [s for s in TOKENS if s in Path(str(source)).stem.lower()]
def score(source,preferred):
 stem=Path(str(source)).stem.lower();p=prefixes(source);cats=categories(source)
 value=(500 if preferred and preferred in p else 0)+max([PRIORITY[x] for x in cats] or [0])
 if any(x in stem for x in ['bnr_','lcs_','index','_id']):value-=300
 return value

def choose(rows,preferred,maximum):
 rows=sorted(rows,key=lambda r:(-score(r['source'],preferred),r['source']))
 if preferred:
  specific=[r for r in rows if preferred in prefixes(r['source'])]
  if specific:rows=specific
 chosen=[];seen=set()
 for row in rows:
  cats=categories(row['source']);category=cats[0] if cats else Path(row['source']).stem
  if category in seen or score(row['source'],preferred)<0:continue
  chosen.append(row);seen.add(category)
  if len(chosen)>=maximum:return chosen
 for row in rows:
  if row not in chosen and score(row['source'],preferred)>=0:chosen.append(row)
  if len(chosen)>=maximum:break
 return chosen

def usable_file(version,path,issues):
 p=Path(path);p=p if p.is_absolute() else version/p
 if not p.is_relative_to(version):issues.append({'output':str(p),'problem':'output path outside version'});return None
 if not p.is_file():issues.append({'output':str(p.relative_to(version)),'problem':'successful index entry output is missing'});return None
 return p

def extract(version,sample=False,max_containers=8,max_sounds=8,max_individual=3):
 files={'textures':version/'texture_stats.json','soundbanks':version/'soundbank_stats.json','individual':version/'individual_texture_stats.json'}
 missing=[str(p.name) for p in files.values() if not p.is_file()]
 if missing:raise ValueError('Required completed indices missing: '+', '.join(missing))
 data={k:read(p) for k,p in files.items()}
 if any(d.get('finished') is False for d in data.values()):raise ValueError('A source index explicitly marks finished=false')
 issues=[];texture_rows=[x for x in data['textures'].get('containers',[]) if x.get('decoded',0)>0]
 banks=[x for x in data['soundbanks'].get('banks',[]) if x.get('decoded',0)>0 and x.get('status') not in ['placeholder_zero','unsupported_signature','empty_bank']]
 individual=[x for x in data['individual'].get('records',[]) if x.get('meta',{}).get('status')=='decoded']
 observed=collections.Counter(p for x in texture_rows+banks+individual for p in prefixes(x['source']))
 preferred=next((p for p in HINTS.get(version.name[:2],[]) if observed[p]),None)
 images=[];sounds=[];individual_images=[]
 layers=sorted({layer(r['source']) for r in texture_rows+banks+individual})
 manifests_opened=0
 for ly in layers:
  limit=max_containers if ly=='official' else min(2,max_containers)
  for container in choose([x for x in texture_rows if layer(x['source'])==ly],preferred,limit):
   mp=version/container['manifest']
   if not mp.is_file():issues.append({'manifest':container['manifest'],'problem':'container manifest missing'});continue
   manifest=read(mp);manifests_opened+=1
   decoded=[r for r in manifest.get('records',[]) if r.get('status')=='decoded' and r.get('output')]
   decoded.sort(key=lambda r:(-max([PRIORITY[x] for x in categories(r.get('name',''))] or [0]),-(r.get('width',0)*r.get('height',0)),r.get('name','')))
   for record in decoded[:2]:
    png=usable_file(version,mp.parent/record['output'],issues)
    if png is None:continue
    images.append({'layer':ly,'source_container':container['source'],'export_manifest':container['manifest'],'internal_name':record.get('name'),'path':str(png.relative_to(version)),'record_kind':record.get('kind'),'width':record.get('width'),'height':record.get('height'),'png_sha256_from_export':record.get('sha256'),'source_name_categories':categories(container['source']),'prefixes_observed':prefixes(container['source']),'claim':'Successfully decoded PNG exists; feature labels come only from actual source/internal names.','verification':{'successful_export_record':True,'output_file_exists':True,'content_rehashed':False}})
  for bank in choose([x for x in banks if layer(x['source'])==ly],preferred,max_sounds):
   mp=version/'extras/soundbanks'/Path(bank['source']).with_suffix('')/'va3_manifest.json'
   if not mp.is_file():issues.append({'manifest':str(mp.relative_to(version)),'problem':'bank manifest missing'});continue
   manifest=read(mp);manifests_opened+=1
   decoded=[r for r in manifest.get('records',[]) if r.get('status')=='decoded' and r.get('output')]
   for record in decoded[:1]:
    wav=usable_file(version,mp.parent/record['output'],issues)
    if wav is None:continue
    sounds.append({'layer':ly,'source_bank':bank['source'],'export_manifest':str(mp.relative_to(version)),'entry':record.get('entry'),'internal_name':record.get('name'),'path':str(wav.relative_to(version)),'audio':record.get('audio'),'channels':record.get('channels'),'sample_rate':record.get('sample_rate'),'source_name_categories':categories(bank['source']),'prefixes_observed':prefixes(bank['source']),'claim':'VA3 record explicitly decoded and WAV exists; sentinel/empty/unsupported banks excluded.','verification':{'successful_export_record':True,'output_file_exists':True,'content_rehashed':False}})
  selected=choose([x for x in individual if layer(x['source'])==ly],preferred,max_individual)
  for item in selected:
   png=usable_file(version,item['output'],issues)
   if png is None:continue
   meta=item['meta'];individual_images.append({'layer':ly,'source':item['source'],'path':str(png.relative_to(version)),'width':meta.get('width'),'height':meta.get('height'),'format':meta.get('format'),'source_sha256':meta.get('source_sha256'),'prefixes_observed':prefixes(item['source']),'claim':'Decoded individual PNG exists; generic emblems are examples of retained resources, not evidence of edition exclusivity.'})
 result={'schema_version':1,'version_directory':version.name,'sample':sample,'selection_complete':not issues,'selection_scope':'Bounded representative examples from completed export indices; not an exhaustive inventory or proof that assets are exclusive to this edition.','preferred_actual_prefix':preferred,'observed_index_prefix_counts':dict(observed),'indices':{k:{'path':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for k,p in files.items()},'decoded_scene_images':images,'decoded_sound_examples':sounds,'decoded_individual_texture_examples':individual_images,'excluded_source_classifications':{'placeholder_soundbank_files':data['soundbanks'].get('placeholder_files',0),'empty_soundbank_files':data['soundbanks'].get('empty_bank_files',0),'unsupported_soundbank_files':data['soundbanks'].get('unsupported_count',0)},'selection_io':{'raw_directories_walked':0,'media_contents_read':0,'representative_manifests_opened':manifests_opened},'issues':issues,'notes':['Existing l44fo/l44av prefixes in newer packages may be carried-over assets. File presence is not proof of a newly introduced feature.','The literal l44avby prefix is used only when actually present in successful indices. No l44ba/l44bt family is invented.','Official and mod_omnimix or other data_mods layers remain separate.','Only index entries with a decoded status plus a real PNG/WAV output are shown; raw-only, empty and sentinel resources are never described as decoded.']}
 name='version-features.sample' if sample else 'version-features';dump(version/(name+'.json'),result)
 lines=[version.name+' 已导出特色资源例证','小样选择。' if sample else '仅代表性选择，不是该版本独有性的证明。','实际优先前缀：'+str(preferred),'']
 for title,key in [('界面/场景PNG','decoded_scene_images'),('已解WAV','decoded_sound_examples'),('单张纹理PNG','decoded_individual_texture_examples')]:
  lines.append(title+'：')
  for item in result[key]:lines.append('['+item['layer']+'] '+str(item.get('internal_name') or Path(item.get('source','')).stem)+' → '+item['path'])
 lines+=['','仅依赖已有成功导出记录与文件存在检查；不遍历raw、不读取音频或图片内容。','原包只有占位或未导出文件的项目不列为已解出。',f'索引/输出诊断：{len(issues)}项。']
 (version/(name+'.txt')).write_text('\n'.join(lines)+'\n');return result

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--versions',nargs='+');p.add_argument('--sample',action='store_true');p.add_argument('--max-containers',type=int,default=8);p.add_argument('--max-sounds',type=int,default=8);p.add_argument('--max-individual',type=int,default=3);args=p.parse_args()
 chosen=[name for name in VERSIONS if not args.versions or any(name==v or name.startswith(v+'_') for v in args.versions)];failed=[]
 for name in chosen:
  try:
   result=extract(args.root.resolve()/name,args.sample,args.max_containers,args.max_sounds,args.max_individual)
   if result['issues']:failed.append({'version':name,'issues':result['issues']})
   print(json.dumps({'version':name,'sample':args.sample,'prefix':result['preferred_actual_prefix'],'png_examples':len(result['decoded_scene_images']),'wav_examples':len(result['decoded_sound_examples']),'individual_png_examples':len(result['decoded_individual_texture_examples']),'issues':len(result['issues'])},ensure_ascii=False),flush=True)
  except Exception as exc:failed.append({'version':name,'error':str(exc)});print(json.dumps(failed[-1],ensure_ascii=False),flush=True)
 return 2 if failed else 0
if __name__=='__main__':sys.exit(main())
