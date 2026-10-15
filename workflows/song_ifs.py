from pathlib import Path
import sys,json,re,hashlib,os,time,traceback,concurrent.futures,collections,threading
ROOT=None
from ifstools.ifs import IFS
from kbinxml import KBinXML
from lxml import etree
from assets.oki4s import bmp_to_wav
from arcade import import_ifs as arcade
from assets.eve import parse_eve_chart
CACHE=None
CACHE_LOCK=threading.Lock()
def dump(path,data):
 path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_name(path.name+'.tmp');tmp.write_bytes(data);tmp.replace(path)
def js(path,obj):dump(path,json.dumps(obj,ensure_ascii=False,indent=2).encode())
def flatten(node):
 if len(node):return {c.tag:flatten(c) for c in node}
 return node.text or ''
def metadata(version):
 records={'official':{}};allmeta=[]
 for p in (version/'raw').rglob('*.xml'):
  if 'music_info' not in str(p):continue
  try:
   d=p.read_bytes();k=KBinXML(d);text=k.to_text();rel=p.relative_to(version/'raw');dest=version/'extras/metadata'/rel
   dump(dest,text.encode());allmeta.append(str(rel))
   for e in k.xml_doc.findall('.//data'):
    mid=e.findtext('music_id')
    if not mid:continue
    r=flatten(e);r['_source']=str(rel);r['_xml_attributes']=dict(e.attrib)
    if r.get('name_string'):
     try:r['name_reading_cp932']=bytes.fromhex(r['name_string']).decode('cp932')
     except Exception:pass
    layer='mod_'+p.parts[p.parts.index('data_mods')+1] if 'data_mods' in p.parts else 'official'
    records.setdefault(layer,{})[mid]=r
  except Exception as e:allmeta.append({'path':str(p),'error':str(e)})
 js(version/'music_metadata.json',records.get('official',{}))
 for layer,items in records.items():
  if layer!='official':js(version/'mods'/layer/'music_metadata.json',items)
 return records,allmeta

def process_song(task):
 version,p,meta=task;mid=p.name.split('_')[0]
 layer='official'
 if 'data_mods' in p.parts:
  layer='mod_'+p.parts[p.parts.index('data_mods')+1]
 song=version/'songs'/mid if layer=='official' else version/'mods'/layer/'songs'/mid
 if (song/'info.json').exists():return json.loads((song/'info.json').read_text())
 row={'id':mid,'layer':layer,'source':str(p.relative_to(version)),'source_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'metadata':meta.get(layer,{}).get(mid,{}),'members':[],'charts':{},'audio':{},'errors':[]}
 i=None
 try:
  i=IFS(str(p));dump(song/'ifs_manifest.xml',i.manifest.to_text().encode())
  for f in i.tree.all_files:
   raw=f.load(convert_kbin=False);name=f.full_path
   if Path(name).is_absolute() or '..' in Path(name).parts:raise ValueError('Unsafe IFS pathname')
   rawpath=song/'original'/name;dump(rawpath,raw)
   member={'name':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()};row['members'].append(member)
   low=f.name.lower()
   if low.endswith('.eve'):
    text=raw.decode('cp932',errors='replace');events=[];unparsed=[]
    for ln,line in enumerate(text.splitlines(),1):
     if not line.strip():continue
     parts=[x.strip() for x in line.split(',')]
     try:
      ev={'tick':int(parts[0]),'command':parts[1],'arguments':parts[2:]}
      if len(parts)>2:
       try:ev['value']=int(parts[2])
       except ValueError:pass
      events.append(ev)
     except (ValueError,IndexError):unparsed.append({'line':ln,'text':line})
    stem=Path(low).stem.lower()
    short=stem[-1] if re.fullmatch(r'\d{8}[abe]',stem) else stem
    code={'b':'BSC','bas':'BSC','bsc':'BSC','a':'ADV','adv':'ADV','e':'EXT','ext':'EXT'}.get(short,stem.upper())
    js(song/(code+'.events.json'),{'tick_hz':300,'events':events,'unparsed_lines':unparsed,'source':name})
    counts=collections.Counter(e['command'] for e in events)
    row['charts'][code]={'events':len(events),'commands':dict(counts),'unparsed_lines':len(unparsed),'sha256':member['sha256'],'playable':bool(counts['PLAY'] or counts['LONG'])}
    if code in ['BSC','ADV','EXT'] and len(raw)>=32:
     try:
      notes,timing,_=parse_eve_chart(raw,code,name)
      mc={'meta':{'mode':4,'version':code,'creator':'jubeat','song':{'title':mid,'artist':'','id':int(mid)},'cover':'','background':''},'time':[{'beat':arcade.beat_tuple(b),'bpm':float(bpm)} for b,bpm in timing],'note':[]}
      for beat,index,endbeat,endindex in notes:
       n={'beat':arcade.beat_tuple(beat),'index':index}
       if endbeat is not None:n.update(endbeat=arcade.beat_tuple(endbeat),endindex=endindex)
       mc['note'].append(n)
      js(song/(code+'.mc'),mc);row['charts'][code]['mc_exported']=True
     except Exception as e:row['charts'][code]['mc_error']=str(e)
   elif raw.startswith(b'BMP\0'):
    stem=Path(low).stem
    role='preview' if stem.endswith(('idx','index','preview')) else 'bgm' if stem.endswith('bgm') else stem
    try:
     key=member['sha256'];wav=CACHE/(key+'.wav');metapath=CACHE/(key+'.json')
     with CACHE_LOCK:
      if not wav.exists() or not metapath.exists():
       audio=bmp_to_wav(raw,wav);js(metapath,audio)
      else:audio=json.loads(metapath.read_text())
     dest=song/(role+'.wav')
     if not dest.exists():os.link(wav,dest)
     row['audio'][role]={**audio,'raw_sha256':key,'path':str(dest.relative_to(version))}
    except Exception as e:row['errors'].append({'member':name,'stage':'audio','error':str(e)})
  row['placeholder']=not row['charts'] and not row['audio']
 except Exception as e:row['errors'].append({'stage':'ifs','error':str(e)})
 finally:
  if i:i.close()
 js(song/'info.json',row);return row

def extras(version):
 rows=[];errors=[]
 for p in (version/'raw').rglob('*.ifs'):
  if p.name.endswith('_msc.ifs'):continue
  rel=p.relative_to(version/'raw');dest=version/'extras/ifs'/rel.with_suffix('')
  if (dest/'extraction-complete.json').exists():continue
  i=None
  try:
   i=IFS(str(p));i.extract(path=str(dest),progress=False,extract_manifest=True,recurse=True,convert_kbin=True)
   js(dest/'extraction-complete.json',{'source':str(rel)});rows.append(str(rel))
  except Exception as e:errors.append({'source':str(rel),'error':str(e)})
  finally:
   if i:i.close()
 js(version/'extras/ifs-extraction.json',{'newly_extracted':rows,'errors':errors});return errors

def run(version):
 print(version.name,'START',flush=True);meta,metafiles=metadata(version)
 js(version/'metadata_sources.json',metafiles)
 files=sorted((version/'raw').rglob('*_msc.ifs'));rows=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
  for n,r in enumerate(ex.map(process_song,[(version,p,meta) for p in files]),1):
   rows.append(r)
   if n%50==0:print(version.name,n,'/',len(files),flush=True)
 errors=extras(version)
 metadata_errors=[{'stage':'metadata',**x} for x in metafiles if isinstance(x,dict) and 'error' in x]
 mc_errors=[{'id':r['id'],'stage':'optional_mc','difficulty':code,'error':c['mc_error']} for r in rows for code,c in r['charts'].items() if 'mc_error' in c]
 stats={'version':version.name,'ifs_song_containers':len(files),'metadata_entries':len(meta.get('official',{})),'song_directories':len(rows),'playable_songs':sum(any(c.get('playable') for c in r['charts'].values()) for r in rows),'placeholder_containers':sum(r.get('placeholder',False) for r in rows),'chart_files':sum(len(r['charts']) for r in rows),'playable_charts':sum(c.get('playable',False) for r in rows for c in r['charts'].values()),'mc_files':sum(c.get('mc_exported',False) for r in rows for c in r['charts'].values()),'bgm_decoded':sum('bgm' in r['audio'] for r in rows),'preview_decoded':sum('preview' in r['audio'] for r in rows),'errors':sum(len(r['errors']) for r in rows),'ifs_extra_errors':len(errors),'metadata_errors':len(metadata_errors),'optional_mc_errors':len(mc_errors),'finished':True}
 stats['by_layer']={}
 for layer in sorted({r.get('layer','official') for r in rows}):
  lr=[r for r in rows if r.get('layer','official')==layer]
  stats['by_layer'][layer]={'containers':len(lr),'playable_songs':sum(any(c.get('playable') for c in r['charts'].values()) for r in lr),'charts':sum(len(r['charts']) for r in lr),'bgm':sum('bgm' in r['audio'] for r in lr),'preview':sum('preview' in r['audio'] for r in lr)}
 js(version/'manifest.json',rows);js(version/'stats.json',stats);js(version/'errors.json',[{'id':r['id'],**e} for r in rows for e in r['errors']]+errors+metadata_errors+mc_errors)
 print(json.dumps(stats),flush=True)
 return stats
if __name__=='__main__':
 from ._common import configure
 ROOT,names=configure(__doc__ or '歌曲 IFS 全资源导出')
 CACHE=ROOT/'_cache/audio';CACHE.mkdir(parents=True,exist_ok=True)
 failed=False
 for name in names:
  stats=run(ROOT/name);failed|=any(stats.get(k,0) for k in ['errors','ifs_extra_errors','metadata_errors','optional_mc_errors'])
 raise SystemExit(2 if failed else 0)
