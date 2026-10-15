"""Validate modern per-song exports through small headers and JSON indexes."""
from pathlib import Path
import json,sys,wave,collections,hashlib

def main():
 global ROOT
 from ._common import configure
 ROOT,names=configure('本地资源批处理')
 for name in names:
  v=ROOT/name;rows=json.loads((v/'manifest.json').read_text());errors=[];counts=collections.Counter()
  for r in rows:
   song=v/'songs'/r['id'] if r.get('layer','official')=='official' else v/'mods'/r['layer']/'songs'/r['id']
   try:
    source=v/r['source']
    if not source.is_file():raise ValueError('Missing original IFS '+str(source))
    for m in r['members']:
     p=song/'original'/m['name']
     if not p.is_file() or p.stat().st_size!=m['bytes']:raise ValueError('Missing/wrong-length raw member '+m['name'])
     counts['original_members']+=1
    for role,a in r['audio'].items():
     p=v/a['path']
     with wave.open(str(p),'rb') as w:
      actual={'frames':w.getnframes(),'sample_rate':w.getframerate(),'channels':w.getnchannels()}
      for k,val in actual.items():
       if k in a and a[k]!=val:raise ValueError(f'{role} {k}: {a[k]} != {val}')
      if w.getsampwidth()!=2:raise ValueError('Expected PCM16')
      header=w.tell();w.setpos(max(0,w.getnframes()-1));tail=w.readframes(1)
      if w.getnframes() and len(tail)!=w.getnchannels()*2:raise ValueError('Truncated WAV '+role)
     counts[role+'_wav_headers']+=1
    for code,c in r['charts'].items():
     events=json.loads((song/(code+'.events.json')).read_text())
     commands=collections.Counter(e['command'] for e in events['events'])
     if len(events['events'])!=c['events'] or dict(commands)!=c['commands']:raise ValueError('Chart index mismatch '+code)
     if c.get('mc_exported'):
      mc=json.loads((song/(code+'.mc')).read_text())
      if sum('index' in n for n in mc['note'])!=commands['PLAY']+commands['LONG']:raise ValueError('MC note count mismatch '+code)
      counts['mc_files']+=1
     counts['event_json_files']+=1
    counts['song_containers']+=1
   except Exception as e:errors.append({'id':r['id'],'layer':r.get('layer','official'),'error':str(e)})
  report={'version':name,'checks':'original member existence/length; PCM16 WAV header and final frame; exact event counts; MC note count; no full audio scan','counts':dict(counts),'errors':errors}
  p=v/'export_audit.json';t=p.with_suffix('.json.tmp');t.write_text(json.dumps(report,ensure_ascii=False,indent=2));t.replace(p)
  print(name,dict(counts),'errors',len(errors),flush=True)
  if errors:raise SystemExit(2)
if __name__=='__main__':main()
