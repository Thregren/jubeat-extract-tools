"""按命名原始EVE重建/核对已导出的MC，不使用IFS固定块顺序。"""
from pathlib import Path
import hashlib,json
from assets.eve import parse_eve_chart
from arcade.import_ifs import beat_tuple

def repair(version):
    rows=json.loads((version/'manifest.json').read_text());changed=[];errors=[];checked=0
    for row in rows:
        song=version/'songs'/row['id'] if row.get('layer','official')=='official' else version/'mods'/row['layer']/'songs'/row['id']
        for code,c in row['charts'].items():
            if not c.get('mc_exported') or code not in ('BSC','ADV','EXT'):continue
            try:
                candidates=[m for m in row['members'] if m.get('sha256')==c['sha256'] and m['name'].lower().endswith('.eve')]
                if not candidates:raise ValueError('No original EVE member matching chart SHA256')
                raw=(song/'original'/candidates[0]['name']).read_bytes()
                if hashlib.sha256(raw).hexdigest()!=c['sha256']:raise ValueError('Original EVE SHA256 mismatch')
                notes,timing,_=parse_eve_chart(raw,code,candidates[0]['name'])
                p=song/(code+'.mc');old=json.loads(p.read_text());new=dict(old)
                new['time']=[{'beat':beat_tuple(b),'bpm':float(bpm)} for b,bpm in timing];new['note']=[]
                # Keep an explicitly attached audio event if the earlier export had one.
                new['note'].extend(n for n in old.get('note',[]) if 'sound' in n and 'index' not in n)
                for beat,index,endbeat,endindex in notes:
                    n={'beat':beat_tuple(beat),'index':index}
                    if endbeat is not None:n.update(endbeat=beat_tuple(endbeat),endindex=endindex)
                    new['note'].append(n)
                checked+=1
                if new!=old:
                    previous_sha=hashlib.sha256(p.read_bytes()).hexdigest();tmp=p.with_suffix('.mc.tmp');tmp.write_text(json.dumps(new,ensure_ascii=False,indent=2));tmp.replace(p)
                    changed.append({'id':row['id'],'layer':row.get('layer','official'),'code':code,'raw_eve_sha256':c['sha256'],'previous_mc_sha256':previous_sha,'new_mc_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'old_playable_notes':sum('index' in n for n in old.get('note',[])),'new_playable_notes':len(notes)})
            except Exception as e:errors.append({'id':row['id'],'code':code,'error':str(e)})
    report={'checked':checked,'changed':changed,'errors':errors,'note':'Only derived MC files replaced atomically; original EVE, tick JSON, audio and metadata remain unchanged.'}
    p=version/'mc_named_member_audit.json';tmp=p.with_suffix('.json.tmp');tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2));tmp.replace(p)
    return report

def main():
    from ._common import configure
    root,names=configure(__doc__);failed=False
    for name in names:
        r=repair(root/name);failed|=bool(r['errors']);print(name,'checked',r['checked'],'repaired',len(r['changed']),'errors',len(r['errors']),flush=True)
    return 2 if failed else 0
if __name__=='__main__':raise SystemExit(main())
