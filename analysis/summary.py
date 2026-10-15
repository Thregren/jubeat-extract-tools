#!/usr/bin/env python3
"""Summarize final JSON indexes only; never opens audio, image, chart or game files.

Default writes 解包汇总.txt and _reports/解包汇总.json. An incomplete run returns
JSON status=partial and exit 2. --allow-partial is useful for scoped validation;
it changes only the exit code, never the reported completeness.
"""
from __future__ import annotations
import argparse,collections,datetime,json,os,re,sys,time
from pathlib import Path

SPECS=[
 ('01','jubeat 初代','release','early'),('02','ripples','release','early'),
 ('03','knit / APPEND','release','early'),('04','copious APPEND','release','early'),
 ('05','saucer','release','middle'),('06','saucer fulfill','release','middle'),
 ('07','prop','release','middle'),('08','Qubell','release','middle'),('09','clan','release','middle'),
 ('10','T44 JAEPO 2020','test_build','t44'),('11','festo','release','modern'),
 ('12','Ave.','release','modern'),('13','beyond the Ave. 基础包','release','modern'),
 ('14','beyond 20260722 增量','increment','modern'),
 ('15','beyond 20260722 合并视图','derived_merge','modern'),
 ('16','jubeat plus','mobile','mobile'),('17','jukebeat','mobile','mobile')]
TUTORIAL_ID='30010011'
ROLES={'BSC.eve','ADV.eve','EXT.eve','bgm.bmpaudio.bin','preview.bmpaudio.bin'}
READ_FILES=[]

def read_json(path,default=None):
 if not path.is_file():return default
 for attempt in range(3):
  try:
   value=json.loads(path.read_text());READ_FILES.append(str(path));return value
  except (OSError,ValueError):
   if attempt==2:raise
   time.sleep(.03)

def integer(value):
 if isinstance(value,bool):return int(value)
 if isinstance(value,(int,float)):return int(value)
 if isinstance(value,(list,dict)):return len(value)
 return None

def first(mapping,*keys,default=None):
 for key in keys:
  if key in mapping and mapping[key]is not None:return mapping[key]
 return default

def numeric(mapping,*keys,default=None):return integer(first(mapping,*keys,default=default))

def records(manifest):
 if isinstance(manifest,list):return manifest
 if isinstance(manifest,dict):
  value=manifest.get('songs',[])
  if isinstance(value,dict):return [dict(v,id=k)for k,v in value.items()]
  return value if isinstance(value,list)else[]
 return []

def modern_classify(rows):
 layers=collections.defaultdict(collections.Counter)
 for row in rows:
  layer=row.get('layer','official');audio=row.get('audio',{});charts=row.get('charts',{})
  complete=all(k in audio for k in ['bgm','preview'])and all(charts.get(k,{}).get('playable')for k in ['BSC','ADV','EXT'])
  if row.get('errors'):status='conversion_error'
  elif complete:status='complete'
  elif 'bgm'in audio and len(audio)==1 and not charts:status='bgm_only'
  elif not audio and not any(c.get('playable')for c in charts.values()):status='raw_only'
  else:status='partial'
  c=layers[layer];c[status]+=1;c['containers']+=1;c['bgm']+=int('bgm'in audio);c['preview']+=int('preview'in audio);c['chart_files']+=len(charts);c['playable_charts']+=sum(bool(v.get('playable'))for v in charts.values())
 return {k:dict(v)for k,v in layers.items()}

def row_tutorial(row):
 return str(row.get('id',row.get('song_id','')))==TUTORIAL_ID

def complete_modern(row):
 return all(k in row.get('audio',{})for k in ['bgm','preview'])and all(row.get('charts',{}).get(k,{}).get('playable')for k in ['BSC','ADV','EXT'])and not row.get('errors')

def count_tutorial(rows,complete_fn):
 return sum(row_tutorial(r)and complete_fn(r)for r in rows)

def per_layer(counters,tutorials=0):
 c=dict(counters);keys=['complete','song_complete','songs_complete'];complete=None if any(k in c and c[k]is None for k in keys)else numeric(c,*keys,default=0)
 return {'complete_five_asset_ids':complete,'known_tutorial_complete_ids':tutorials,
  'non_tutorial_complete_ids':max(0,complete-tutorials)if complete is not None else None,
  'bgm_only_ids':numeric(c,'bgm_only','bgm_only_containers',default=0),
  'raw_only_ids':numeric(c,'raw_only','raw_only_containers',default=0),
  'partial_ids':numeric(c,'partial_resources','partial','partial_resource_containers',default=0),
  'conversion_error_ids':numeric(c,'conversion_error','failed',default=0),
  'verified_dummy_ids':numeric(c,'dummy_placeholders',default=0),'opaque_raw_only_ids':numeric(c,'opaque_containers',default=0),
  'containers':numeric(c,'containers'),'bgm_audio':numeric(c,'bgm','bgm_audio'),
  'original_preview_audio':numeric(c,'preview','original_preview_audio'),
  'original_chart_files':numeric(c,'chart_files','charts'),
  'playable_chart_files':numeric(c,'playable_charts','charts_with_notes'),
  'source_classification':dict(counters)}

def extras_counter(stats,extras):
 return {'png_files':numeric(stats,'texture_png','images_decoded','covers',default=numeric(extras,'texture_png')),
  'cover_song_ids':numeric(stats,'songs_with_cover',default=numeric(extras,'song_ids_with_covers')),
  'extra_audio_files':numeric(stats,'extra_audio_wav','sound_bank_audio_decoded',default=numeric(extras,'va3_wav')),
  'extra_chart_files':numeric(stats,'extra_chart_json',default=0),
  'bank_audio_files':numeric(stats,'sound_bank_audio_decoded',default=numeric(extras,'va3_wav'))}

def assess(root,spec,path,middle_final):
 idx,label,kind,adapter=spec
 result={'index':idx,'label':label,'kind':kind,'adapter':adapter,'directory':str(path.relative_to(root))if path else None,'state':'missing','complete':False,'completion_evidence':[],'layers':{},'counters':{},'warnings':[]}
 if path is None:return result
 try:
  stats=read_json(path/'stats.json',{});manifest=read_json(path/'manifest.json',None);rows=records(manifest)
  result['source_files']={name:str((path/name).relative_to(root))for name in ['stats.json','manifest.json','errors.json','textures.json','extras-stats.json','texture_stats.json','soundbank_stats.json','individual_texture_stats.json','cover_provenance_audit.json','mc-stats.json','version-features.json','README.md','说明.txt']if(path/name).is_file()}
  result['raw_stats']=stats
  build_match=re.search(r'_(\d{10})(?:_|$)',path.name)
  result['build_code']=first(stats,'build',default=build_match.group(1)if build_match else None)
  if not stats or manifest is None:
   result.update(state='in_progress');result['warnings'].append('最终 stats.json / manifest.json 尚未齐备。');return result
  errors=read_json(path/'errors.json',[])
  result['errors_report_count']=integer(errors)or 0
  result['stats_error_count']=numeric(stats,'errors_total','errors',default=0)or 0
  extras=read_json(path/'extras-stats.json',{})
  result['raw_extra_stats']=extras
  texture_stats=read_json(path/'texture_stats.json',{})
  soundbank_stats=read_json(path/'soundbank_stats.json',{})
  mc_stats=read_json(path/'mc-stats.json',{})
  individual_stats=read_json(path/'individual_texture_stats.json',{})
  cover_audit=read_json(path/'cover_provenance_audit.json',{})
  result['raw_individual_texture_stats']=individual_stats;result['raw_cover_provenance_audit']=cover_audit
  result['raw_texture_stats']=texture_stats;result['raw_soundbank_stats']=soundbank_stats;result['raw_mc_stats']=mc_stats
  result['metadata_record_count']=numeric(stats,'metadata_songs','metadata_entries','songs_with_metadata')
  result['extra_assets']=extras_counter(stats,extras)
  if result['extra_assets']['png_files']is None:
   tx=numeric(texture_stats,'decoded_texture_images','png_decoded');ind=numeric(individual_stats,'textures_decoded','png_decoded')
   result['extra_assets']['png_files']=(tx or 0)+(ind or 0)if tx is not None or ind is not None else None
  if result['extra_assets']['bank_audio_files']is None:result['extra_assets']['bank_audio_files']=numeric(soundbank_stats,'decoded_sounds')
  if result['extra_assets']['extra_audio_files']is None:result['extra_assets']['extra_audio_files']=numeric(soundbank_stats,'decoded_sounds')
  result['extra_assets']['complete_ids_with_covers']=numeric(stats,'songs_complete_with_cover')
  result['extra_assets']['complete_ids_without_covers']=numeric(stats,'songs_complete_without_cover')
  result['extra_assets']['texture_source_placeholders']=numeric(extras,'texture_placeholders',default=0)
  result['extra_assets']['soundbank_source_placeholders']=numeric(soundbank_stats,'placeholder_files',default=numeric(extras,'va3_placeholders',default=0))
  result['extra_assets']['soundbank_unsupported_files']=numeric(soundbank_stats,'unsupported_count',default=0)
  legacy_individual=read_json(path/'individual-texture-manifest.json',{}) if adapter=='middle' else {}
  standalone=numeric(legacy_individual.get('stats',{}),'png_decoded',default=0)
  if adapter=='middle' and result['extra_assets']['png_files'] is not None:result['extra_assets']['png_files']+=standalone
  result['extra_assets']['individual_texture_png_files']=standalone if adapter=='middle' else numeric(individual_stats,'textures_decoded','png_decoded',default=0)
  result['extra_assets']['png_count_scope']='song covers only'if adapter=='t44'else'decoded texture/crop and standalone TEX PNG; excludes duplicate cover links and general IFS inventories'
  result['stage_error_counts']={stage:{k:integer(v)for k,v in values.items()if(k=='errors'or k.endswith('_errors'))and integer(v)is not None}for stage,values in [('extras',extras),('textures',texture_stats),('soundbanks',soundbank_stats),('mc',mc_stats),('individual_textures',individual_stats),('cover_provenance',cover_audit)]}
  result['declared_song_or_id_directories']=numeric(stats,'songs','song_directories','song_directories_total')
  finished=False
  if adapter=='early':
   complete=numeric(stats,'complete_five_asset_ids');tutorial=count_tutorial(rows,lambda r:ROLES<=r.get('selected_resources',{}).keys())
   bgm_only=sum(set(r.get('selected_resources',{}))=={'bgm.bmpaudio.bin'}for r in rows)
   with_cover=sum(ROLES<=r.get('selected_resources',{}).keys()and bool(r.get('covers'))for r in rows)
   result['extra_assets']['complete_ids_with_covers']=with_cover
   result['extra_assets']['complete_ids_without_covers']=(complete-with_cover)if complete is not None else None
   c={'complete':complete,'containers':numeric(stats,'songs'),'bgm':numeric(stats,'bgm_wav'),'preview':numeric(stats,'preview_wav'),'chart_files':numeric(stats,'source_chart_files','charts'),'playable_charts':numeric(stats,'charts_with_play_or_long','charts'),'bgm_only':bgm_only,'partial':max(0,(numeric(stats,'partial_or_special_id_count',default=0)or 0)-bgm_only)}
   result['layers']={'official':per_layer(c,tutorial)}
   result['source_asset_gaps']=stats.get('source_asset_gaps',[])
   result['known_tutorial_complete_ids']=[TUTORIAL_ID]if tutorial else[]
   result['pak_integrity']={k:stats.get(k)for k in ['pak_entries','pak_md5_verified','pak_recovered_names','pak_name_double_hash_verified']}
   finished=bool(stats.get('finished')and stats.get('pipeline_complete'))
   if finished:result['completion_evidence']=['stats.finished=true','stats.pipeline_complete=true','谱面/音频/纹理/额外资源索引已完成']
   if idx=='01':result['warnings'].append('初代没有独立 music_info；59为完整数字ID资源组，另6份未关联EVE单独保留，不据此虚构正式曲目身份。')
  elif adapter=='middle':
   classes=stats.get('resource_classes',{});complete=numeric(stats,'songs_complete','song_complete')
   tutorial=0
   for r in rows:
    if not row_tutorial(r):continue
    single=read_json(path/r.get('path','songs/30010011/manifest.json'),{})
    if single:
     kinds={a.get('kind')for a in single.get('audio',[])}
     cs=single.get('charts',[])
     tutorial=int({'bgm','preview'}<=kinds and len(cs)>=3 and all(not c.get('placeholder_or_empty')for c in cs))
   c={'complete':complete,'containers':numeric(stats,'ifs_total'),'bgm_only':numeric(stats,'bgm_only_containers',default=numeric(classes,'bgm_only',default=0)),'raw_only':numeric(stats,'raw_only_containers',default=numeric(classes,'raw_only',default=0)),'partial_resources':numeric(stats,'partial_resource_containers',default=numeric(classes,'partial_resources','partial',default=0)),'conversion_error':numeric(classes,'failed',default=0),'bgm':numeric(stats,'bgm'),'preview':numeric(stats,'preview'),'chart_files':numeric(stats,'charts'),'playable_charts':numeric(stats,'charts_with_notes'),'dummy_placeholders':numeric(stats,'dummy_placeholders',default=0),'opaque_containers':numeric(stats,'opaque_containers',default=0)}
   result['layers']={'official':per_layer(c,tutorial)};result['resource_classes']=classes
   # The old transitional ifs_complete meant "contains BGM", so it is deliberately ignored.
   finished=bool(middle_final and classes and complete is not None)
   if finished:result['completion_evidence']=['_tools/middle/final-summary.json exists','stats.resource_classes and songs_complete finalized']
   else:result['warnings'].append('中期全部收尾标记尚未齐备；过渡字段 ifs_complete 不作为完整歌曲数。')
  elif adapter=='t44':
   cs=numeric(stats,'songs_with_charts');c={'complete':cs,'containers':numeric(stats,'song_directories'),'bgm':cs if numeric(stats,'song_audio_decoded')==2*(cs or 0)else None,'preview':cs if numeric(stats,'song_audio_decoded')==2*(cs or 0)else None,'chart_files':numeric(stats,'charts_parsed'),'playable_charts':numeric(stats,'charts_parsed')}
   result['layers']={'official':per_layer(c,0)}
   result['special_video_directories']=numeric(stats,'special_video_directories',default=0)
   finished=(cs==20 and numeric(stats,'charts_parsed')==60 and numeric(stats,'song_audio_decoded')==40 and numeric(stats,'ifs_extracted')==numeric(stats,'ifs_containers')and numeric(stats,'ifs_extracted_files')==22404 and numeric(stats,'unconverted_song_assets',default=0)==0)
   if finished:result['completion_evidence']=['20 songs / 60 original charts / 40 song audio','60/60 IFS extracted, 22404 members','unconverted_song_assets=0']
  else:
   classes=stats.get('resource_classification')
   if not classes:
    classes=modern_classify(rows);result['classification_derived_from_manifest']=True
   if adapter=='modern'and not stats.get('resource_classification')and idx!='14':
    result['provisional_resource_classification']=classes
    classes={layer:dict(c,complete=None)for layer,c in classes.items()}
   for layer,c in classes.items():
    tutorial=0 if adapter=='mobile'else count_tutorial([r for r in rows if r.get('layer','official')==layer],complete_modern)
    result['layers'][layer]=per_layer(c,tutorial)
   if adapter=='mobile':
    # Mobile IDs are a separate 9-digit namespace; no arcade correspondence is inferred.
    result['id_namespace']='mobile 9-digit ID; retained verbatim'
    result['app_version']=stats.get('app_version')
    result['audio_output']='original AAC/M4A; no lossy re-encode'
    expected=numeric(stats,'expected_song_packages');processed=numeric(stats,'processed_song_packages')
    finished=bool(stats.get('finished')and stats.get('status')=='complete'and not stats.get('limited_sample',False)and expected is not None and processed==expected)
    result['mobile_source_packages']={'expected':expected,'processed':processed}
    finished=finished and len(rows)==processed and stats.get('supplementary_images_finalized',True)
    result['raw_five_assets_preserved']=numeric(stats,'raw_five_assets_preserved',default=sum(all(k in r.get('audio',{})for k in ['bgm','preview'])and all(k in r.get('charts',{})for k in ['BSC','ADV','EXT'])for r in rows))
    result['three_playable_chart_complete_ids']=numeric(stats,'complete_five_assets',default=sum(complete_modern(r)for r in rows))
    result['zero_note_charts']=numeric(stats,'zero_note_charts',default=0)
    result['zero_note_chart_details']=[{'id':str(r.get('id')),'title':r.get('title'),'difficulty':code,'commands':ch.get('commands',{})}for r in rows for code,ch in r.get('charts',{}).items()if 'commands'in ch and not ch.get('commands',{}).get('PLAY',0)and not ch.get('commands',{}).get('LONG',0)]
    result['extra_assets']['song_image_files']=numeric(stats,'images_decoded')
    result['extra_assets']['mobile_extra_image_files']=numeric(stats,'extras_images_decoded',default=0)
    result['extra_assets']['png_files']=(numeric(stats,'images_decoded',default=0)or 0)+(numeric(stats,'extras_images_decoded',default=0)or 0)
    result['extra_assets']['png_count_scope']='song images plus independently reported mobile extras images'
    result['mobile_extras']={k:stats.get(k)for k in ['scimg_exported','marker_packages','theme_packages','themes_exported','images_decoded','image_failures','extras_images_decoded','marker_frame_images','theme_images_decoded']if k in stats}
    result['original_preview_missing_ids']=[str(r.get('id'))for r in rows if 'preview'not in r.get('audio',{})]
    result['original_preview_missing_count']=numeric(stats,'preview_missing',default=len(result['original_preview_missing_ids']))
    result['original_preview_failed_count']=numeric(stats,'preview_failed',default=0)
    result['limited_sample']=bool(stats.get('limited_sample',False))
    if finished:result['completion_evidence']=['stats.finished=true',f'processed_song_packages={processed} / expected={expected}']
   else:
    # Classification is written last by root. The already completed patch can also
    # be validated from its two final extra-stage stats while classification is pending.
    final_classified=bool(stats.get('resource_classification'))
    required=['texture_stats.json','soundbank_stats.json']if idx=='14'else['texture_stats.json','soundbank_stats.json','individual_texture_stats.json','cover_provenance_audit.json']
    pending=[name for name in required if not(path/name).is_file()]
    if idx!='14'and not final_classified:pending.append('resource_classification')
    stage_indexes={'texture_stats.json':texture_stats,'soundbank_stats.json':soundbank_stats,'individual_texture_stats.json':individual_stats,'cover_provenance_audit.json':cover_audit}
    for name in required:
     if(path/name).is_file():
      data=stage_indexes[name]
      if not isinstance(data.get('errors'),list):pending.append(name+': missing final error list')
      if data.get('finished')is False:pending.append(name+': unfinished')
    for stage,values in result['stage_error_counts'].items():
     if stage in ['textures','soundbanks','individual_textures','cover_provenance']and any(values.values()):pending.append(stage+': errors')
    result['pending_final_stages']=pending
    finished=bool(stats.get('finished')and not pending)
    if finished:result['completion_evidence']=['stats.finished=true']+required+(['resource_classification finalized']if final_classified else['patch classification derived read-only'])
    else:result['warnings'].append('仍待最终分类/纹理/音库/独立纹理/封面来源审计；单独finished或分类字段均不代表全部完成。')
  # Derive only from indexes, never by opening exported media.
  result['counters']={key:sum(c[key]for c in result['layers'].values()if isinstance(c.get(key),int))if all(c.get(key)is not None for c in result['layers'].values())else None for key in ['complete_five_asset_ids','known_tutorial_complete_ids','non_tutorial_complete_ids','bgm_only_ids','raw_only_ids','partial_ids','conversion_error_ids','verified_dummy_ids','opaque_raw_only_ids','bgm_audio','original_preview_audio','original_chart_files','playable_chart_files']}
  result['conversion_error_ids']=sum(c.get('conversion_error_ids',0)or 0 for c in result['layers'].values())
  error_present=bool(result['errors_report_count']or result['stats_error_count']or result['conversion_error_ids'])
  if any(n for stage in result['stage_error_counts'].values()for n in stage.values()):error_present=True
  if adapter=='mobile'and any(numeric(stats,k,default=0)for k in ['image_failures','preview_failed','bgm_failed']):error_present=True
  if adapter=='modern'and stats.get('finished')and not finished:
   result['waiting_classification']=not bool(stats.get('resource_classification'));result['waiting_final_stages']=True
  result['state']='complete_with_errors'if finished and error_present else'complete'if finished else'waiting_classification'if result.get('waiting_classification')else'waiting_final_stages'if result.get('waiting_final_stages')else'in_progress'
  result['complete']=bool(finished and not error_present)
  if kind=='increment':result['warnings'].append('增量资源，仅包含修改/新增/占位；不能当完整独立曲库，也不与合并视图相加。')
  if kind=='derived_merge':result['warnings'].append('来自13基础包与14增量的派生视图，不算新增正式版本或独立来源曲库。')
 except Exception as exc:
  result.update(state='index_read_error',complete=False);result['warnings'].append(f'{type(exc).__name__}: {exc}')
 return result

def features(root,versions,published):
 byid={r['index']:r for r in versions};out=[]
 def add(idx,title,detail,evidence):
  r=byid.get(idx)
  if r and r.get('complete'):out.append({'title':title,'detail':detail,'evidence':evidence})
 add('01','初代额外系统内容','除歌曲外另导出19段音频和6份附加EVE；包含bgm_title、bgm_scale_check、bgm_phase_check。6份附加EVE只有4种字节内容，其中4份没有PLAY，另两份分别32/28个PLAY，未无证据命名。',['01_jubeat_2008120500/extras_manifest.json'])
 add('03','knit APPEND独立层','APPEND为24个新ID、120个成员，全部MD5匹配；base与append都保留，明确APPEND优先，未覆盖原层。',['03_knit_2011011400/pak_manifest.json','03_knit_2011011400/manifest.json'])
 if byid.get('03',{}).get('complete')and byid.get('04',{}).get('complete'):
  out.append({'title':'教程30010011','detail':'Knit与Copious都保留30010011教程演奏段（BSC/ADV/EXT为44/8/8个PLAY）。静态断言xref明确MUSIC_ID_TUTORIAL_PLAY；Knit教程进/出演示音频为30010010/30010012，Copious对应40010010/40010012。','evidence':['_tools/early/tutorial_id_evidence.json','_tools/early/knit_tutorial_id_disasm.txt']})
 t=byid.get('10',{})
 if t.get('complete'):
  st=t['raw_stats'];ms=read_json(root/t['directory']/'manifest.json',[]);hold_examples=[]
  for r in records(ms):
   for code,ch in r.get('charts',{}).items():
    if ch.get('holds',0):hold_examples.append(f"{r.get('title',r.get('id'))} {code}：{ch['holds']} LONG")
  out.append({'title':'T44 JAEPO测试内容','detail':f"20首、60谱；共{st.get('holds')}条LONG，另有WMV视频目录、122段音库片段及22404个IFS成员。"+(' 例：'+hold_examples[0]if hold_examples else''),'evidence':[t['directory']+'/manifest.json',t['directory']+'/stats.json',t['directory']+'/说明.txt']})
 for idx in ['05','06','07','08','09']:
  r=byid.get(idx,{})
  if r.get('complete'):
   vf=read_json(root/r['directory']/'version-features.json',{})
   if vf:
    observations=vf.get('observed_features',[])[:3];examples=vf.get('examples',[])[:2]
    detail='；'.join(observations)
    if examples:detail+='。例：'+'、'.join(str(e.get('label',e.get('internal_name',e.get('kind'))))for e in examples)
    out.append({'title':vf.get('title',r['label']+'的资源例证'),'detail':detail,'evidence':[r['directory']+'/version-features.json'],'examples':[{'kind':e.get('kind'),'label':e.get('label',e.get('internal_name')),'source':e.get('source'),'exported':e.get('exported'),'internal_name':e.get('internal_name')}for e in vf.get('examples',[])],'version_feature_data':vf})
   else:
    out.append({'title':r['label']+'的额外资源','detail':'纹理/音库和封面独立保留，详细计数见extras-stats；具体特色索引尚未写入。','evidence':[r['directory']+'/extras-stats.json']})
 # Modern feature indices use explicit successful-export examples rather than
 # the middle-generation observed_features/examples schema. Read only this bounded
 # JSON; the producer already checked outputs and carries source-index hashes.
 for idx in ['11','12','13','14']:
  r=byid.get(idx,{})
  if not r.get('complete'):continue
  vf=read_json(root/r['directory']/'version-features.json',{})
  if not vf or vf.get('sample'):continue
  groups=[('texture','decoded_scene_images','source_container'),('soundbank','decoded_sound_examples','source_bank'),('individual_texture','decoded_individual_texture_examples','source')]
  examples=[]
  for kind,key,source_key in groups:
   for e in vf.get(key,[]):
    examples.append({'kind':kind,'label':e.get('internal_name')or Path(e.get(source_key,'')).stem,'layer':e.get('layer','official'),'source':e.get(source_key),'exported':e.get('path'),'export_manifest':e.get('export_manifest'),'internal_name':e.get('internal_name'),'verification':e.get('verification'),'producer_claim':e.get('claim')})
  if not examples:continue
  totals={kind:sum(e['kind']==kind for e in examples)for kind,_,_ in groups}
  detail=f"已成功导出的本包资源例证：场景/界面PNG {totals['texture']}个、WAV {totals['soundbank']}个、独立纹理PNG {totals['individual_texture']}个。仅代表性选择，不据此断定首发时间或本代独有；旧前缀素材可能随包保留，official与mod来源层分别标注。"
  if vf.get('issues'):detail+=f" 例证选择诊断{len(vf['issues'])}项，详该JSON。"
  out.append({'title':r['label']+'已导出资源例证','detail':detail,'evidence':[r['directory']+'/version-features.json'],'examples':examples,'version_feature_data':vf})
 r=byid.get('11',{})
 if r.get('complete'):
  out.append({'title':'festo官方层与Omnimix','detail':'按来源层分别统计完整组、BGM-only与占位，Omnimix不并入official。层名：'+', '.join(r['layers']),'evidence':[r['directory']+'/manifest.json',r['directory']+'/stats.json']})
 if byid.get('14',{}).get('complete'):
  out.append({'title':'beyond增量与合并','detail':'14是增量，15为13基础包加14同ID覆盖的派生视图；增量未出现的ID不视为删除。dummy覆盖时撤销旧派生谱面/音频，保留原始层。三个目录不相加充当版本数或曲库规模。','evidence':['_reports/merge-behavior-validation.json',byid['14']['directory']+'/manifest.json']})
 for idx in ['16','17']:
  r=byid.get(idx,{})
  if r.get('complete'):
   e=r.get('mobile_extras',{});detail=f"原始9位ID与街机8位ID分开；BGM AAC与原preview.m4a分别导出，App版本未从当前证据确定。歌曲图片{r.get('extra_assets',{}).get('song_image_files')}张，另有extras图片{r.get('extra_assets',{}).get('mobile_extra_image_files')}张；marker帧{e.get('marker_frame_images',0)}、主题图{e.get('theme_images_decoded',0)}、scImg导出{e.get('scimg_exported',0)}。原试听缺失ID数：{r.get('original_preview_missing_count',len(r.get('original_preview_missing_ids',[])))}。"
   if r.get('zero_note_charts'):detail+=' 100050031《魔法陣より愛をこめて》BSC/ADV原seq仅有MEASURE/HAKU/TEMPO/END，EXT正常，故原始五件套1388组、三谱均可玩1387组。'
   out.append({'title':r['label']+'移动版','detail':detail,'evidence':[r['directory']+'/manifest.json',r['directory']+'/stats.json']})
 return out

def text_report(report):
 c=report['coverage'];lines=['jubeat资源解包汇总',f"状态：{report['status']}；已完成 {c['complete_directories']}/{c['expected_directories']} 个预期输出目录。"]
 if report['status']!='complete':lines.append('本报告是阶段性快照；未完成/未评估项目：'+', '.join(c['pending_indices']))
 lines += ['','口径：街机完整组按最终资源分类（本地BGM、原试听和三难度可用谱齐备）；移动端分别列原始五件套与三谱可玩组；这不是当前菜单配信数，也不保证等于完整官方曲库。未在music_info列出的实际资源仍保留。“非教程”只扣除有证据的教程，不通过metadata过滤曲库。','17个目录包含12个街机世代的独立包、T44测试包、1个beyond增量、1个合并视图、2个移动App；增量与合并视图不另算正式世代。','各版不跨版去重，不相加当作总歌曲数。Omnimix单列；BGM-only、dummy/raw-only、partial与metadata-only均不冒充完整歌曲。','','编号 | 内容/层 | 状态 | 完整组(非教程+教程；移动原始/可玩) | BGM | 原试听 | 原谱 | PNG']
 def n(value):return '—'if value is None else str(value)
 for r in report['versions']:
  state={'complete':'完成','in_progress':'处理中','missing':'未生成','not_assessed':'本次未评估','complete_with_errors':'已跑完但有错误','waiting_classification':'待最终分类','waiting_final_stages':'待最终素材/来源审计','index_read_error':'索引未就绪'}.get(r['state'],r['state'])
  if not r['layers']:
   lines.append(f"{r['index']} | {r['label']} | {state} | — | — | — | — | —");continue
  for layer_index,(k,v)in enumerate(r['layers'].items()):
   name=r['label']+(' ('+str(r['build_code'])+')'if r.get('build_code')else'')+(' / '+k if len(r['layers'])>1 or k not in ['official','mobile']else'')
   full=n(v['complete_five_asset_ids'])
   if r.get('adapter')=='mobile':full=f"{n(r.get('raw_five_assets_preserved'))}原始/{n(r.get('three_playable_chart_complete_ids'))}三谱可玩"
   elif v['complete_five_asset_ids']is not None:full+=f" ({n(v['non_tutorial_complete_ids'])}+{n(v['known_tutorial_complete_ids'])})"
   suffix=''if r.get('complete')else'〔暂计〕'
   lines.append(f"{r['index']} | {name} | {state} | {full}{suffix} | {n(v['bgm_audio'])} | {n(v['original_preview_audio'])} | {n(v['original_chart_files'])} | {n(r.get('extra_assets',{}).get('png_files'))if layer_index==0 else'见本目录总计'}{'封面'if r.get('adapter')=='t44'else''}")
   other=[]
   for key,label in [('bgm_only_ids','仅BGM'),('raw_only_ids','仅原始/占位或未知'),('partial_ids','部分/特殊'),('conversion_error_ids','转换失败')]:
    if v.get(key):other.append(f'{label}{v[key]}')
   if other:lines.append('     '+', '.join(other))
   if v.get('verified_dummy_ids')or v.get('opaque_raw_only_ids'):lines.append(f"     已确认1字节dummy：{v.get('verified_dummy_ids',0)}；其他未知raw-only：{v.get('opaque_raw_only_ids',0)}")
  ea=r.get('extra_assets',{})
  if ea.get('extra_audio_files')or ea.get('extra_chart_files'):lines.append(f"     额外音频{n(ea.get('extra_audio_files'))}，额外谱面{n(ea.get('extra_chart_files'))}；"+('PNG此处仅计歌曲封面。'if r.get('adapter')=='t44'else'PNG按纹理/裁剪与独立TEX统计，不重复计封面链接；通用IFS文件另见索引。'))
  if ea.get('texture_source_placeholders')or ea.get('soundbank_source_placeholders'):lines.append(f"     源资源占位：纹理{ea.get('texture_source_placeholders',0)}、音库{ea.get('soundbank_source_placeholders',0)}；未计入成功解码数。")
  if ea.get('complete_ids_with_covers')is not None:lines.append(f"     完整组中已导出本包封面{ea.get('complete_ids_with_covers')}，无本包封面{ea.get('complete_ids_without_covers')}；未从其他版本补图。")
  if r.get('kind')=='mobile':lines.append('     原AAC/M4A保留；preview.m4a单独计数，缺失原试听ID：'+str(r.get('original_preview_missing_count',len(r.get('original_preview_missing_ids',[]))))+'；App版本未知。原谱0note张数：'+str(r.get('zero_note_charts',0))+'；有源seq但无PLAY/LONG不当成漏解。')
  if r.get('errors_report_count')or r.get('stats_error_count'):lines.append(f"     错误索引记录{r['errors_report_count']}，stats错误计数{r['stats_error_count']}；详该版errors.json。")
 lines += ['','已确认的具体内容：']
 for f in report['features']:
  lines.append('• '+f['title']+'：'+f['detail'])
  # Include a bounded mix of concrete paths; retain every example and all producer
  # provenance in JSON. Do not stat or read media from this reporting pass.
  by_kind=collections.defaultdict(list)
  for e in f.get('examples',[]):
   if e.get('source')and e.get('exported'):by_kind[e.get('kind','other')].append(e)
  for kind,examples in by_kind.items():
   e=next((x for x in examples if x.get('layer','official')=='official'),examples[0])
   lines.append('    '+('['+e['layer']+'] 'if e.get('layer')else'')+str(e.get('label')or e.get('internal_name')or kind)+'：'+e['source']+' → '+e['exported'])
   if e.get('export_manifest'):lines.append('      导出记录：'+e['export_manifest'])
 lines += ['','保留与转换：原始PAK/IFS/JBT、原EVE/seq和300Hz整数tick事件JSON保留。MC是另附的拍值量化导出，不取代原谱。WAV/FLAC为原压缩音源的PCM重建，AAC为源音频提取，均不宣称恢复录音母带。试听来自原index/preview资源，没有用完整BGM裁切冒充。移动9位ID不映射成街机8位ID。','未识别的文件名/二进制或配套资源缺席均如实留在manifest/errors或特殊资产清单；“同ID缺少某成员”不自动等于包损坏。']
 pub=report['tool_validation'].get('published',{})
 if pub:
  repo=pub.get('repository','https://github.com/Thregren/jubeat-extract-tools');commit=pub.get('commit','e6312b6')
  lines += ['',f"工具验证：{repo}/commit/{commit}（已推送工具；{pub.get('tests_passed','未知')}项测试通过）。本地真歌验证：_reports/preview-feature-real-validation.json；比较与合并规则验证见相应JSON。未上传游戏资源。"]
 lines += ['','详细逐版stats、分层分类、完成证据、限制及验证链接：'+report['outputs']['json'],'本汇总仅读JSON索引，没有遍历或解码音频。']
 return '\n'.join(lines)+'\n'

def atomic_write(path,text):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+'.tmp');tmp.write_text(text);tmp.replace(path)

def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--only',help='Comma-separated two-digit indices for scoped validation; global status stays partial.');p.add_argument('--out-txt',type=Path);p.add_argument('--out-json',type=Path);p.add_argument('--allow-partial',action='store_true');args=p.parse_args(argv)
 root=args.root.resolve();selected={s.strip().zfill(2)for s in args.only.split(',')}if args.only else{s[0]for s in SPECS};unknown=selected-{s[0]for s in SPECS}
 if unknown:p.error('Unknown indices: '+','.join(sorted(unknown)))
 dirs=collections.defaultdict(list)
 for item in root.iterdir():
  if item.is_dir()and re.match(r'^\d{2}_',item.name):dirs[item.name[:2]].append(item)
 mf=read_json(root/'_tools/middle/final-summary.json',None)
 versions=[]
 for spec in SPECS:
  idx,label,kind,adapter=spec
  if idx not in selected:
   versions.append({'index':idx,'label':label,'kind':kind,'adapter':adapter,'directory':None,'state':'not_assessed','complete':False,'layers':{}});continue
  if len(dirs[idx])>1:
   versions.append({'index':idx,'label':label,'kind':kind,'adapter':adapter,'directory':None,'state':'index_read_error','complete':False,'layers':{},'warnings':['Multiple directories share this index; no arbitrary choice made.'],'candidates':[str(x)for x in dirs[idx]]});continue
  versions.append(assess(root,spec,dirs[idx][0]if dirs[idx]else None,mf))
 txt=(args.out_txt if args.out_txt else root/'解包汇总.txt');js=(args.out_json if args.out_json else root/'_reports/解包汇总.json')
 if not txt.is_absolute():txt=root/txt
 if not js.is_absolute():js=root/js
 pub=read_json(root/'_reports/github-preview-feature.json',{})
 validation={k:read_json(root/'_reports'/k,{})for k in ['preview-feature-real-validation.json','compare-tool-validation.json','merge-behavior-validation.json']}
 validation['published']=pub;validation['links']={'github_commit':pub.get('repository','https://github.com/Thregren/jubeat-extract-tools')+'/commit/'+pub.get('commit','e6312b6'),'real_song':str(root/'_reports/preview-feature-real-validation.json'),'comparison':str(root/'_reports/compare-tool-validation.json'),'merge_rules':str(root/'_reports/merge-behavior-validation.json')}
 completed=sum(r['complete']for r in versions);pending=[r['index']for r in versions if not r['complete']]
 report={'schema_version':1,'generated_at':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec='seconds'),'status':'complete'if completed==len(SPECS)else'partial','root':str(root),'outputs':{'txt':str(txt),'json':str(js)},'coverage':{'expected_directories':len(SPECS),'assessed_indices':sorted(selected),'complete_directories':completed,'pending_indices':pending,'scoped_validation':bool(args.only)},'counting_rules':{'complete_five_assets':'arcade final usable-resource class; mobile additionally separates raw five assets from three-playable-chart complete groups; never a menu/distribution or authoritative official-catalog count','non_tutorial_complete':'complete groups minus evidenced tutorial ID 30010011; metadata absence does not filter assets','official_mod_separation':True,'increment_and_merge_are_not_extra_release_generations':True,'mobile_id_namespace':'retain original 9 digits separately from arcade 8 digits','previews':'original index/preview assets only; never assumed present or cut from BGM','audio':'source AAC retained or source compressed audio decoded into PCM WAV/FLAC; no studio master recovery','charts':'original EVE/seq and 300Hz event JSON preserved; MC is separate beat-quantized output'},'versions':versions,'tool_validation':validation}
 report['features']=features(root,versions,pub);report['index_files_read']=sorted(set(READ_FILES));report['media_files_opened']=0
 atomic_write(js,json.dumps(report,ensure_ascii=False,indent=2)+'\n');atomic_write(txt,text_report(report))
 print(json.dumps({'status':report['status'],'complete_directories':completed,'expected_directories':len(SPECS),'pending_indices':pending,'txt':str(txt),'json':str(js),'media_files_opened':0},ensure_ascii=False))
 return 0 if report['status']=='complete'or args.allow_partial else 2
if __name__=='__main__':raise SystemExit(main())
