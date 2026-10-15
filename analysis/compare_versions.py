#!/usr/bin/env python3
"""Compare local manifest identities and raw 300 Hz EVE events, without opening audio.

Do not interpret absent resources as a commercial release's deleted songs.
Formal run: python3 -m analysis.compare_versions
Bounded verification: --versions 01 10 14 --limit 2 --sample
"""
from pathlib import Path
import argparse,collections,hashlib,json,re,sys,datetime,struct

DIFFICULTIES=('BSC','ADV','EXT')
ALGORITHM='jubeat-events-300hz-v3'

def digest(raw):return hashlib.sha256(raw).hexdigest()
def write_json(path,value):
 path.parent.mkdir(parents=True,exist_ok=True)
 path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def read_json(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def semantic_events(raw):
 """Preserve event order, ticks, commands, numeric values and all extra fields.

Only byte order mark, encoding, newlines, surrounding whitespace and decimal
integer spelling are normalized. Unknown commands are preserved. A malformed
nonempty row makes semantic comparison unavailable rather than dropping data.
 """
 errors=[]
 try:text=raw.decode('utf-8-sig')
 except UnicodeDecodeError:
  try:text=raw.decode('cp932')
  except UnicodeDecodeError as e:return {'algorithm':ALGORITHM,'available':False,'error':str(e)}
 events=[];commands=collections.Counter()
 for number,line in enumerate(text.lstrip('\ufeff').splitlines(),1):
  if not line.strip():continue
  fields=[s.strip() for s in line.split(',')]
  if len(fields)<2 or not re.fullmatch(r'[+-]?\d+',fields[0]) or not fields[1]:
   errors.append({'line':number,'text':line});continue
  event=[int(fields[0]),fields[1]]
  for value in fields[2:]:
   event.append(int(value) if re.fullmatch(r'[+-]?\d+',value) else value)
  events.append(event);commands[fields[1]]+=1
 if errors:return {'algorithm':ALGORITHM,'available':False,'unparsed_lines':errors}
 canonical=json.dumps({'tick_hz':300,'events':events},ensure_ascii=False,separators=(',',':')).encode('utf-8')
 gameplay=json.dumps({'tick_hz':300,'events':[e for e in events if e[1] not in ('HAKU','MEASURE')]},ensure_ascii=False,separators=(',',':')).encode('utf-8')
 return {'gameplay_sha256':digest(gameplay),'algorithm':ALGORITHM,'available':True,'sha256':digest(canonical),'events':len(events),'commands':dict(commands),'playable_notes':commands['PLAY']+commands['LONG']}


def semantic_seq(raw):
 if len(raw)<0x60:return {'algorithm':ALGORITHM,'available':False,'reason':'binary seq header truncated'}
 count=struct.unpack_from('<I',raw,4)[0]
 if count>2000000 or 0x60+count*8>len(raw):return {'algorithm':ALGORITHM,'available':False,'reason':'binary seq record bounds invalid'}
 names={1:'PLAY',2:'END',3:'MEASURE',4:'HAKU',5:'TEMPO',6:'LONG'};events=[];commands=collections.Counter()
 for i in range(count):
  r0,value=struct.unpack_from('<II',raw,0x60+8*i);kind=r0&255;cmd=names.get(kind,f'TYPE_{kind}');commands[cmd]+=1;events.append([r0>>8,cmd,value])
 canonical=json.dumps({'tick_hz':300,'events':events},ensure_ascii=False,separators=(',',':')).encode()
 gameplay=json.dumps({'tick_hz':300,'events':[e for e in events if e[1] not in ('HAKU','MEASURE')]},ensure_ascii=False,separators=(',',':')).encode()
 return {'algorithm':ALGORITHM,'available':True,'sha256':digest(canonical),'gameplay_sha256':digest(gameplay),'events':len(events),'commands':dict(commands),'playable_notes':commands['PLAY']+commands['LONG'],'source_format':'mobile_binary_seq','header_magic_hex':raw[:4].hex(),'trailing_bytes':len(raw)-(0x60+8*count)}

def normalize_layer(layer):
 s=str(layer or 'official')
 if s in ('official','base','selected','original'):return 'official'
 if s.lower()=='omnimix':return 'mod_omnimix'
 return s

def chart_code(name):
 m=re.search(r'(?:^|/)(bsc|adv|ext)(?:\.eve)?$',str(name),re.I)
 return m[1].upper() if m else None

def short_metadata(song):
 m=song.get('metadata') or {}
 if isinstance(m,dict) and 'children' in m:
  m={c['tag']:c.get('value',c.get('text','')) for c in m['children'] if c.get('tag')}
 result={k:song[k] for k in ('title','artist','resource_class','status') if song.get(k) is not None}
 if isinstance(m,dict):
  for k in ('music_name','title','artist_name','parent_id','is_hold','version','detail_level_bsc','detail_level_adv','detail_level_ext','level_bsc','level_adv','level_ext'):
   if k in m:result[k]=m[k]
 return result

class Reader:
 def __init__(self,root,cache):
  self.root=root;self.cache=cache;self.counters=collections.Counter();self.issues=[]
 def semantic(self,sha,path):
  if not path or not path.is_file():
   self.counters['eve_paths_unavailable']+=1;return {'algorithm':ALGORITHM,'available':False,'reason':'raw EVE path not present'}
  if path.stat().st_size>16*1024*1024:
   return {'algorithm':ALGORITHM,'available':False,'reason':'EVE exceeds 16 MiB read bound'}
  raw=path.read_bytes();self.counters['eve_files_read']+=1;self.counters['eve_bytes_read']+=len(raw);actual=digest(raw)
  if sha and actual!=sha:
   self.issues.append({'type':'manifest_hash_mismatch','path':str(path),'declared':sha,'actual':actual})
   return {'algorithm':ALGORITHM,'available':False,'reason':'manifest raw hash mismatch','actual_raw_sha256':actual}
  if actual in self.cache:
   self.counters['semantic_cache_hits']+=1;return self.cache[actual]
  result=semantic_seq(raw) if path.suffix.lower()=='.seq' else semantic_events(raw);result['actual_raw_sha256']=actual;self.cache[actual]=result;return result
 def path(self,folder,relative,candidates=()):
  values=[relative,*candidates]
  for value in values:
   if not value:continue
   p=Path(value);p=p if p.is_absolute() else folder/p
   if p.is_file():return p
  return None
 def record(self,folder,song,layer,code,sha,path,info=None,provenance=None):
  info=info or {};sem=self.semantic(sha,path)
  if not sem.get('available'):
   self.issues.append({'type':'chart_semantics_unavailable','version':folder.name,'id':song.get('song_id',song.get('id')),'layer':layer,'difficulty':code,'path':str(path) if path else None,'problem':sem})
  if not sha:sha=sem.get('actual_raw_sha256')
  commands=info.get('commands') or info.get('chart_statistics') or sem.get('commands') or {}
  playable=info.get('playable')
  if playable is None and commands:playable=commands.get('PLAY',0)+commands.get('LONG',0)>0
  if playable is None and info.get('notes') is not None:playable=info['notes']>0
  metadata=short_metadata(song)
  level=info.get('level_interpretation',(song.get('levels') or {}).get(code,metadata.get('detail_level_'+code.lower(),metadata.get('level_'+code.lower()))))
  return {'level':level,'layer':layer,'difficulty':code,'raw_sha256':sha,'semantic_sha256':sem.get('sha256'),'gameplay_sha256':sem.get('gameplay_sha256'),'semantic_available':sem.get('available',False),'semantic_algorithm':ALGORITHM,'events':sem.get('events',info.get('events')),'commands':commands,'playable':playable,'placeholder_or_empty':info.get('placeholder_or_empty',False),'raw_eve':str(path.relative_to(folder)) if path and path.is_relative_to(folder) else str(path) if path else None,'source_layer':provenance or layer,'semantic_problem':None if sem.get('available') else sem.get('reason',sem.get('error',sem.get('unparsed_lines')))}
 def load(self,folder,limit=None,song_ids=None):
  manifest=folder/'manifest.json'
  data=read_json(manifest);rows=data.get('songs',[]) if isinstance(data,dict) else data
  if not isinstance(rows,list):raise ValueError('manifest songs is not a list')
  if song_ids:rows=[s for s in rows if str(s.get('song_id',s.get('id',''))) in song_ids]
  if limit:rows=rows[:limit]
  songs=[]
  for summary in rows:
   s=summary
   # Middle generations publish small index rows referring to per-song manifests.
   if isinstance(s,dict) and s.get('path') and not any(k in s for k in ('members','charts','selected_resources','assets','layers')):
    detail=self.path(folder,s['path'])
    if detail:
     s=read_json(detail);songdir=detail.parent
    else:self.issues.append({'type':'song_manifest_unavailable','version':folder.name,'entry':s});continue
   else:
    sid=str(s.get('song_id',s.get('id','')));ly=normalize_layer(s.get('layer'))
    songdir=folder/('mods/'+ly+'/songs/'+sid if ly.startswith('mod_') else 'songs/'+sid)
   sid=str(s.get('song_id',s.get('id','')))
   if not re.fullmatch(r'\d{8,12}',sid):self.issues.append({'type':'non_song_id','version':folder.name,'id':sid});continue
   layer=normalize_layer(s.get('layer'))
   if 'layer' not in s and 'omnimix' in str(s.get('source','')).lower():layer='mod_omnimix'
   normalized={'id':sid,'platform':s.get('platform','arcade'),'metadata':short_metadata(s),'source':s.get('source',s.get('source_ifs')),'merge_source_version':s.get('merge_source_version'),'layers':{},'placeholder':bool(s.get('placeholder')),'resource_class':s.get('resource_class',s.get('status'))}
   charts=[]
   if 'selected_resources' in s:
    # Preserve selected official output as well as each original base/append layer.
    selected=s.get('selected_resources',{})
    for name,entry in selected.items():
     code=chart_code(name)
     if code:
      p=self.path(folder,entry.get('path'));charts.append(self.record(folder,s,'official',code,entry.get('sha256'),p,entry,entry.get('layer')))
    for original_layer,resources in (s.get('layers') or {}).items():
     for name,value in resources.items():
      code=chart_code(name)
      if not code:continue
      relative=value.get('path') if isinstance(value,dict) else value
      entry=value if isinstance(value,dict) else selected.get(name,{}) if selected.get(name,{}).get('layer')==original_layer else {}
      p=self.path(folder,relative);charts.append(self.record(folder,s,'archive_'+original_layer,code,entry.get('sha256'),p,entry,original_layer))
   elif isinstance(s.get('charts'),list):
    members={m.get('name'):m for m in s.get('members',[])}
    for chart in s['charts']:
     code=chart_code(chart.get('member',chart.get('raw','')))
     if not code:continue
     member=members.get(chart.get('member'),{});p=self.path(songdir,chart.get('raw'))
     charts.append(self.record(folder,s,layer,code,member.get('sha256'),p,chart))
    normalized['placeholder']=normalized['placeholder'] or (not charts and any(m.get('name')=='dummy.dat' for m in s.get('members',[])))
   else:
    members={m.get('name'):m for m in s.get('members',[])}
    assets={Path(a.get('path','')).name:a for a in s.get('assets',[])}
    for code,chart in (s.get('charts') or {}).items():
     code=code.upper()
     if code not in DIFFICULTIES:continue
     name=code.lower()+'.eve';asset=assets.get(name,{});member=members.get(name,{})
     sha=chart.get('sha256') or member.get('sha256') or asset.get('sha256')
     relative=chart.get('raw') if s.get('platform')=='mobile' else 'raw/data/'+asset['path'] if asset else None
     p=self.path(folder,relative,[songdir/'original'/name,songdir/'original'/(code+'.eve'),songdir/'charts'/name,songdir/name])
     charts.append(self.record(folder,s,layer,code,sha,p,chart))
    normalized['placeholder']=normalized['placeholder'] or (not charts and any(m.get('name')=='dummy.dat' for m in s.get('members',[])))
   for chart in charts:normalized['layers'].setdefault(chart['layer'],{})[chart['difficulty']]=chart
   # Preserve placeholder/audio-only observations even without any EVE.
   normalized['layers'].setdefault(layer,{})
   normalized['playable']=any(c.get('playable') for c in charts if not c['layer'].startswith('archive_'))
   songs.append(normalized)
  match=re.match(r'(\d+)_',folder.name);n=int(match[1]) if match else 999
  kind='mobile_package' if n in (16,17) else 'prototype' if n==10 else 'update_package' if n==14 else 'merged_update' if n==15 else 'release_package'
  stats=read_json(folder/'stats.json') if (folder/'stats.json').is_file() else None
  if isinstance(stats,dict) and (stats.get('finished') is False or stats.get('limited_sample') or stats.get('status') in ('partial','failed','supplementary_image_decode_pending','supplementary_image_decode_in_progress')):
   self.issues.append({'type':'version_not_complete','version':folder.name,'finished':stats.get('finished'),'status':stats.get('status'),'limited_sample':stats.get('limited_sample')})
  evidence=[]
  for p in [folder/'extras/unique-content.json',folder/'unique-content.json',folder/'unique_content.json']:
   if p.is_file():
    v=read_json(p);evidence.append({'source':str(p.relative_to(self.root)),'content':v})
  return {'directory':folder.name,'number':n,'kind':kind,'manifest':str(manifest.relative_to(self.root)),'manifest_sha256':digest(manifest.read_bytes()),'sample_limited':limit is not None,'manifest_rows_seen':len(rows),'songs':songs,'stats':stats,'distinctive_evidence':evidence}

def layer_songs(version,layer):
 return {s['id']:s for s in version['songs'] if layer in s['layers']}

def compare(a,b,layer='official'):
 left=layer_songs(a,layer);right=layer_songs(b,layer);shared=sorted(left.keys()&right.keys())
 result={'from':a['directory'],'to':b['directory'],'layer':layer,'observed_songs_before':len(left),'observed_songs_after':len(right),'shared_song_ids':len(shared),'chart_comparison_scope':'shared_song_ids_only','newly_observed_song_ids':sorted(right.keys()-left.keys()),'not_observed_in_later_package':sorted(left.keys()-right.keys()),'placeholder_to_playable_song_ids':[],'playable_to_placeholder_song_ids':[],'chart_status_counts':{},'chart_changes':[],'metadata_changes':[],'level_changes':[]}
 counts=collections.Counter()
 for sid in shared:
  x,y=left[sid],right[sid]
  if x['placeholder'] and y['playable']:result['placeholder_to_playable_song_ids'].append(sid)
  if x['playable'] and y['placeholder']:result['playable_to_placeholder_song_ids'].append(sid)
  fields={}
  for key in x['metadata'].keys()&y['metadata'].keys():
   if x['metadata'][key]!=y['metadata'][key]:fields[key]={'before':x['metadata'][key],'after':y['metadata'][key]}
  if fields:result['metadata_changes'].append({'id':sid,'fields':fields})
  xc=x['layers'][layer];yc=y['layers'][layer]
  for code in DIFFICULTIES:
   old,new=xc.get(code),yc.get(code)
   if not old and not new:continue
   if not old:status='newly_observed_chart'
   elif not new:status='chart_not_observed_in_later_package'
   elif not old.get('raw_sha256') or not new.get('raw_sha256'):status='raw_identity_unavailable'
   elif old.get('raw_sha256') and old['raw_sha256']==new.get('raw_sha256'):status='raw_identical'
   elif old.get('semantic_sha256') and old['semantic_sha256']==new.get('semantic_sha256'):status='serialization_only'
   elif old.get('gameplay_sha256') and old['gameplay_sha256']==new.get('gameplay_sha256'):status='guide_marker_events_only'
   elif old.get('semantic_sha256') and new.get('semantic_sha256'):status='event_sequence_changed'
   else:status='raw_changed_semantics_unavailable'
   if old and new and old.get('level') is not None and new.get('level') is not None:
    try:level_diff=float(old['level'])!=float(new['level'])
    except (TypeError,ValueError):level_diff=old['level']!=new['level']
    if level_diff:result['level_changes'].append({'id':sid,'difficulty':code,'before':old['level'],'after':new['level']})
   if old and new and old.get('placeholder_or_empty') and new.get('playable'):status='placeholder_to_playable_chart'
   counts[status]+=1
   if status!='raw_identical':result['chart_changes'].append({'id':sid,'difficulty':code,'status':status,'before':old,'after':new})
 result['chart_status_counts']=dict(counts)
 return result

def variants(versions):
 grouped=collections.defaultdict(list)
 for v in versions:
  for s in v['songs']:
   for layer,charts in s['layers'].items():
    for code,c in charts.items():
     grouped[(layer,s['id'],code)].append({'version':v['directory'],'kind':v['kind'],'raw_sha256':c['raw_sha256'],'semantic_sha256':c['semantic_sha256'],'gameplay_sha256':c.get('gameplay_sha256'),'source_layer':c['source_layer'],'merge_source_version':s.get('merge_source_version')})
 out=[]
 for (layer,sid,code),items in sorted(grouped.items()):
  raw={x['raw_sha256'] for x in items if x['raw_sha256']};semantic={x['semantic_sha256'] for x in items if x['semantic_sha256']}
  if len(raw)>1:out.append({'layer':layer,'id':sid,'difficulty':code,'raw_variants':len(raw),'semantic_variants':len(semantic),'gameplay_variants':len({x['gameplay_sha256'] for x in items if x.get('gameplay_sha256')}),'observations':items})
 return out


def mobile_matches(versions):
 arcade=collections.defaultdict(lambda:collections.defaultdict(set));out=[]
 for v in versions:
  if v['kind']!='release_package':continue
  for s in v['songs']:
   for code,c in s['layers'].get('official',{}).items():
    if c.get('playable') and c.get('gameplay_sha256'):arcade[(code,c['gameplay_sha256'])][s['id']].add(v['directory'])
 for v in versions:
  if v['kind']!='mobile_package':continue
  for s in v['songs']:
   for code,c in s['layers'].get('mobile',{}).items():
    candidates=arcade.get((code,c.get('gameplay_sha256')),{});
    if c.get('playable') and candidates:out.append({'mobile_version':v['directory'],'mobile_id':s['id'],'difficulty':code,'gameplay_sha256':c['gameplay_sha256'],'arcade_candidates':[{'arcade_id':sid,'versions':sorted(vs)} for sid,vs in sorted(candidates.items())],'meaning':'Exact normalized gameplay-event match only; original platform-specific IDs remain independent.'})
 return out

def text_report(report):
 lines=['跨版本歌曲与谱面差异（本地文件证据）','样本验证结果，不能代表全部曲库。' if report['sample'] else '正式比较结果。' if report['completion']=='complete' else '部分比较结果（仍有manifest或13→15更新分析未齐）。', '缺失仅表示该包未观察到，不等同于官方删曲。原版、Omnimix、原始append/base层分别比较。','事件哈希保留300 Hz整数tick和原事件顺序；只忽略换行、空白、整数书写差异。另列仅HAKU/MEASURE引导标记变化，避免误报为可玩谱面改动。','']
 for v in report['versions']:
  counts=collections.Counter(layer for s in v['songs'] for layer in s['layers']);charts=collections.Counter(layer for s in v['songs'] for layer,items in s['layers'].items() for c in items)
  lines.append(f"{v['directory']} [{v['kind']}]：歌曲ID {len(set(s['id'] for s in v['songs']))}；层内观测 {dict(counts)}；谱面 {dict(charts)}")
 lines.append('\n正式代际相邻包比较（不把T44、14补丁、15合并版计为独立正式代际）：')
 for p in report['release_package_comparisons']:
  lines.append(f"{p['from']} → {p['to']} [{p['layer']}]：共同ID {p['shared_song_ids']}；后包新见 {len(p['newly_observed_song_ids'])}；后包未见 {len(p['not_observed_in_later_package'])}；谱面 {p['chart_status_counts']}")
 update=report['base_to_merged_update']
 lines.append('\n13底包 → 15合并更新：')
 if update.get('status')=='waiting_for_manifests':lines.append('等待13与15的完整manifest，不从14增量包推断删曲。')
 else:
  lines.append(f"新增可观察ID {len(update['newly_observed_song_ids'])}；占位替换为可玩 {len(update['placeholder_to_playable_song_ids'])}；谱面 {update['chart_status_counts']}")
  lines.append('新增ID：'+','.join(update['newly_observed_song_ids']))
  lines.append('占位替换ID：'+','.join(update['placeholder_to_playable_song_ids']))
 lines.append(f"\n跨包原始哈希有差异的谱面组：{len(report['chart_variant_groups'])}；详细ID、难度、层、原始/事件哈希见JSON。")
 lines.append('I/O统计：'+json.dumps(report['io_statistics'],ensure_ascii=False))
 lines.append(f"manifest不一致/缺失等诊断：{len(report['issues'])}项。")
 return '\n'.join(lines)+'\n'

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True);parser.add_argument('--versions',nargs='+');parser.add_argument('--song-ids',nargs='+');parser.add_argument('--limit',type=int);parser.add_argument('--sample',action='store_true');parser.add_argument('--output-prefix',type=Path);args=parser.parse_args()
 if (args.limit or args.song_ids) and not args.sample:parser.error('--limit/--song-ids requires --sample so a partial run cannot be published as complete')
 root=args.root.resolve();out=root/'_reports';out.mkdir(exist_ok=True)
 cache_path=out/'.chart_semantics_cache.json';cache=read_json(cache_path) if cache_path.is_file() else {}
 if cache.get('algorithm')!=ALGORITHM:cache={'algorithm':ALGORITHM,'raw_sha256':{}}
 reader=Reader(root,cache['raw_sha256']);folders=sorted(p for p in root.iterdir() if p.is_dir() and re.match(r'\d\d_',p.name))
 if args.versions:folders=[p for p in folders if any(p.name==name or p.name.startswith(name+'_') for name in args.versions)]
 versions=[]
 for folder in folders:
  if not (folder/'manifest.json').is_file():reader.issues.append({'type':'version_manifest_unavailable','version':folder.name});continue
  versions.append(reader.load(folder,args.limit,set(args.song_ids) if args.song_ids else None))
  print(json.dumps({'loaded':folder.name,'songs':len(versions[-1]['songs']),'issues':len(reader.issues),'io_statistics':dict(reader.counters)},ensure_ascii=False),flush=True)
 official=[v for v in versions if v['kind']=='release_package'];pairs=[]
 for a,b in zip(official,official[1:]):pairs.append(compare(a,b))
 layer_pairs=[]
 layers=sorted({ly for v in versions for s in v['songs'] for ly in s['layers'] if ly!='official'})
 for layer in layers:
  relevant=[v for v in versions if any(layer in s['layers'] for s in v['songs']) and v['kind']=='release_package']
  for a,b in zip(relevant,relevant[1:]):layer_pairs.append(compare(a,b,layer))
 prototypes=[]
 for v in versions:
  if v['kind']!='prototype':continue
  before=[a for a in official if a['number']<v['number']];after=[a for a in official if a['number']>v['number']]
  if before:prototypes.append(compare(before[-1],v))
  if after:prototypes.append(compare(v,after[0]))
 base=next((v for v in versions if v['number']==13),None);merged=next((v for v in versions if v['number']==15),None)
 update=compare(base,merged) if base and merged else {'status':'waiting_for_manifests','required':['13 base manifest','15 merged manifest']}
 report={'schema':1,'completion':('sample' if args.sample else 'complete' if not reader.issues and update.get('status')!='waiting_for_manifests' else 'partial'),'generated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'sample':args.sample,'scope_notes':['Comparisons describe these local package observations; absence is not proof of commercial removal.','T44 is a prototype. Patch14, merged15 and mobile16/17 are excluded as independent official arcade generations. Original mobile IDs are never numerically rewritten to arcade IDs.','Official/mod layers are separate. Early selected official charts retain base/append provenance; archive layers are additionally preserved.','Audio is never opened. Every referenced bounded EVE/SEQ source is read and its actual SHA-256 is verified against the manifest before semantic-cache reuse.','The normalized sequence preserves every parsed command and order, including HAKU/MEASURE/END; it does not quantize beats or discard unknown commands. A second gameplay hash excludes only HAKU/MEASURE to identify guide-marker-only changes.'],'versions':versions,'release_package_comparisons':pairs,'other_layer_comparisons':layer_pairs,'prototype_comparisons':prototypes,'base_to_merged_update':update,'mobile_package_comparisons':[compare(a,b,'mobile') for a,b in zip([v for v in versions if v['kind']=='mobile_package'],[v for v in versions if v['kind']=='mobile_package'][1:])],'cross_platform_gameplay_candidates':mobile_matches(versions),'chart_variant_groups':variants(versions),'io_statistics':dict(reader.counters),'issues':reader.issues}
 prefix=args.output_prefix or out/('跨版本歌曲与谱面差异.sample' if args.sample else '跨版本歌曲与谱面差异')
 write_json(prefix.with_suffix(prefix.suffix+'.json') if prefix.suffix else prefix.with_suffix('.json'),report)
 txtpath=Path(str(prefix)+'.txt');txtpath.write_text(text_report(report));write_json(cache_path,cache)
 print(json.dumps({'versions':[v['directory'] for v in versions],'sample':args.sample,'variant_groups':len(report['chart_variant_groups']),'io_statistics':report['io_statistics'],'issues':len(reader.issues),'output_prefix':str(prefix)},ensure_ascii=False))
 return 0 if not reader.issues and (args.sample or report['completion']=='complete') else 2
if __name__=='__main__':sys.exit(main())
