#!/usr/bin/env python3
"""仅下载网页曲库JSON谱面作元数据核对；绝不请求音频、封面或缩略图。"""
from __future__ import annotations
import argparse
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from workflows.browser_library import load, save, relative


def code(value): return 'BSC' if value == 'BAS' else value


def count_key(song):
    return tuple(sorted((code(c['code']),c.get('notes',0),c.get('holds',0)) for c in song['charts']))


def cache_path(cache, path):
    return Path(cache)/(hashlib.sha256(path.encode()).hexdigest()+'.json')


def chart_paths(index):
    paths=set()
    for song in index['songs']:
        for c in song['charts']:
            path=song.get('assets',{}).get('chart:'+c['code'])
            if not path:
                stem=song['id'][:-4] if song['id'].endswith('.mcz') else song['id']
                path=f'data/charts/{stem}/{c["code"]}.json'
            path=relative(path)
            if not path.startswith('data/charts/') or not path.endswith('.json'):
                raise ValueError('只允许下载data/charts下的JSON谱面')
            paths.add(path)
    return sorted(paths)


def download(index, base, cache, jobs=4):
    parsed=urlsplit(base)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('网页根地址必须是不含凭证的HTTPS地址')
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    paths=chart_paths(index)
    def one(path):
        dest=cache_path(cache,path)
        if dest.is_file():
            data=load(dest)
            if isinstance(data.get('time'),list) and isinstance(data.get('note'),list):return None
        for attempt in range(3):
            try:
                request=Request(base.rstrip('/')+'/'+quote(path,safe='/'),headers={'Accept-Encoding':'gzip','User-Agent':'jubeat-metadata-chart-audit/1'})
                with urlopen(request,timeout=45) as response:
                    final=urlsplit(response.url)
                    if final.netloc != parsed.netloc:raise ValueError('拒绝跨域重定向')
                    raw=response.read(8*1024*1024+1)
                    if len(raw)>8*1024*1024:raise ValueError('谱面过大')
                    if response.headers.get('Content-Encoding')=='gzip':raw=gzip.decompress(raw)
                    if len(raw)>8*1024*1024:raise ValueError('谱面展开过大')
                data=json.loads(raw)
                if not isinstance(data.get('time'),list) or not isinstance(data.get('note'),list):raise ValueError('不是JSON谱面')
                save(dest,data);return None
            except Exception as error:
                if attempt==2:return dict(path=path,error=str(error))
                time.sleep(.25*(attempt+1))
    errors=[]
    with ThreadPoolExecutor(max_workers=max(1,min(jobs,8))) as pool:
        for i,error in enumerate(pool.map(one,paths),1):
            if error:errors.append(error)
            if i%200==0:print('网页JSON谱面',i,'/',len(paths),'失败',len(errors),flush=True)
    report=dict(charts=len(paths),errors=errors,downloadedMedia=False)
    save(cache/'download-report.json',report)
    return report


def chart_notes(data):
    def beat(value):return value[0]+value[1]/value[2]
    tempos=sorted((beat(e['beat']),float(e['bpm'])) for e in data.get('time',[])) or [(0,120)]
    if tempos[0][0]>0:tempos.insert(0,(0,tempos[0][1]))
    segments=[];seconds=0
    for i,(point,bpm) in enumerate(tempos):
        if i:seconds+=(point-tempos[i-1][0])*60/tempos[i-1][1]
        segments.append((point,seconds,bpm))
    starts=[s[0] for s in segments]
    def sec(value):
        b=beat(value);start,t,bpm=segments[max(0,bisect_right(starts,b)-1)]
        return t+(b-start)*60/bpm
    notes=[]
    for note in data['note']:
        if note.get('type',0)==1 or 'index' not in note:continue
        notes.append((int(note['index']),sec(note['beat']),
            sec(note['endbeat']) if 'endbeat' in note else None,
            note.get('endindex') if 'endbeat' in note else None))
    notes.sort(key=lambda n:(n[0],n[1],n[2] or -1,n[3] or -1))
    return notes


def compare(left,right,tolerance=.02):
    a,b=chart_notes(left),chart_notes(right)
    if len(a)!=len(b):return None
    if not a:return dict(heads=0,maxTimingErrorSeconds=0,constantShiftSeconds=0,comparison='both empty; no identity evidence on its own')
    # 只用于认曲与等级核对：不据此合并/替换本地谱面；容许兼容MC拍点量化。
    shift=min(n[1] for n in a)-min(n[1] for n in b)
    errors=[]
    for x,y in zip(a,b):
        if x[0]!=y[0] or x[3]!=y[3] or (x[2] is None)!=(y[2] is None):return None
        errors.append(abs(x[1]-y[1]-shift))
        if x[2] is not None:errors.append(abs(x[2]-y[2]-shift))
    maximum=max(errors,default=0)
    if maximum>tolerance:return None
    return dict(heads=len(a),maxTimingErrorSeconds=maximum,constantShiftSeconds=shift,
                comparison='all playable heads/holds/tail directions; constant origin shift allowed; no local chart edits')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--index',type=Path,required=True);p.add_argument('--base',required=True)
    p.add_argument('--cache',type=Path,required=True);p.add_argument('--jobs',type=int,default=4)
    a=p.parse_args();report=download(load(a.index),a.base,a.cache,a.jobs)
    print(json.dumps(report,ensure_ascii=False));return 1 if report['errors'] else 0
if __name__=='__main__':raise SystemExit(main())
