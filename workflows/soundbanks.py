"""Export real VA3W banks; retain and explicitly classify non-bank sentinels.

Exactly one 0x00 byte is an observed zero placeholder, not a decoded bank.
Other unsupported signatures, empty/truncated files and unsupported VA3W
variants remain errors with their source length and header retained.
"""
from pathlib import Path
import sys,json,hashlib,collections
from assets.va3 import extract_va3

def atomic_json(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 temp=path.with_name(path.name+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temp.replace(path)

def inspect_source(path):
 """Read only the size and first 28 bytes; hash only the one-byte sentinel."""
 path=Path(path);size=path.stat().st_size
 with path.open('rb')as f:header=f.read(28)
 evidence={'bytes':size,'header_hex':header.hex()}
 if size==1 and header==b'\0':
  return dict(evidence,status='placeholder_zero',sha256=hashlib.sha256(header).hexdigest(),reason='Exactly one 0x00 byte; observed non-audio sentinel, not VA3W and not decoded.')
 if size==0:return dict(evidence,status='unsupported_empty',reason='Empty file; no VA3W header and no audio data.')
 if header[:4]!=b'VA3W':return dict(evidence,status='unsupported_signature',reason='Unsupported soundbank signature; original source retained.')
 if size<28:return dict(evidence,status='unsupported_truncated_va3w',reason='VA3W signature but shorter than the verified 28-byte header.')
 return dict(evidence,status='va3w')

def process_version(name):
 v=ROOT/name;rows=[];placeholders=[];unsupported=[];errors=[];all_sources=[]
 old=v/'soundbank_stats.json';backup=v/'soundbank_stats.before-placeholder-classification.json'
 if old.exists()and not backup.exists():backup.write_bytes(old.read_bytes())
 for p in sorted((v/'raw').rglob('*.va3')):
  rel=p.relative_to(v/'raw');out=v/'extras/soundbanks'/rel.with_suffix('');mark=out/'va3_manifest.json'
  try:
   inspected=inspect_source(p);evidence=dict(source=str(rel),**inspected)
   if inspected['status']=='placeholder_zero':
    placeholders.append(evidence);all_sources.append(evidence)
    atomic_json(out/'placeholder_manifest.json',dict(evidence,source_preserved=str(p.relative_to(v)),decoded=False))
    continue
   if inspected['status']!='va3w':
    unsupported.append(evidence);all_sources.append(evidence);errors.append(dict(evidence,error=inspected['reason']))
    atomic_json(out/'unsupported_manifest.json',dict(evidence,source_preserved=str(p.relative_to(v)),decoded=False))
    continue
   cached=mark.exists();r=json.loads(mark.read_text())if cached else extract_va3(p,out)
   decoded=sum(x.get('status')=='decoded'for x in r['records']);empty=sum(x.get('status')=='empty'for x in r['records'])
   status='partial'if r['errors']else'decoded'if decoded else'empty_bank'
   row=dict(source=str(rel),count=r['count'],decoded=decoded,empty_entries=empty,errors=len(r['errors']),status=status,cache_used=cached,source_bytes=inspected['bytes'],header_hex=inspected['header_hex'])
   rows.append(row);all_sources.append(row)
   errors.extend({'source':str(rel),**e}for e in r['errors'])
  except Exception as exc:
   error={'source':str(rel),'error':str(exc),'status':'decode_error'}
   errors.append(error);all_sources.append(error)
 stats={'schema_version':2,'finished':True,'source_files':len(all_sources),'banks':rows,
  'valid_va3w_banks':len(rows),'decoded_banks':sum(r['status']=='decoded'for r in rows),
  'decoded_sounds':sum(r['decoded']for r in rows),'empty_bank_files':sum(r['status']=='empty_bank'for r in rows),
  'placeholder_files':len(placeholders),'placeholders':placeholders,
  'unsupported_count':len(unsupported),'unsupported_files':unsupported,'errors':errors,
  'source_classifications':all_sources,
  'classification_notes':'One-byte zero sentinels are recorded as placeholders and never counted as decoded banks/sounds. Every other unsupported or truncated source remains an error. Original .va3 files are unchanged. Shared VA3W/OKI4S decoder was not modified.'}
 atomic_json(v/'soundbank_stats.json',stats)
 print(name,'VA3W banks',len(rows),'decoded sounds',stats['decoded_sounds'],'zero placeholders',len(placeholders),'unsupported',len(unsupported),'errors',len(errors),flush=True)
 return stats


def main():
 global ROOT
 from ._common import configure
 ROOT,names=configure('本地资源批处理')
 failed=False
 for name in names:failed|=bool(process_version(name)['errors'])
 return 2 if failed else 0
if __name__=='__main__':raise SystemExit(main())
