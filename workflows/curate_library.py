#!/usr/bin/env python3
"""按网页JSON谱面证据校正元数据、统一版本目录，追加独立本地目录版本。"""
from __future__ import annotations
import argparse
from collections import defaultdict, Counter
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import wave
import struct
from workflows.browser_library import load, save, inside, relative, link_file, digest
from workflows.web_metadata import code, count_key, cache_path, compare

SOURCE_VERSIONS = {
 '01':'jubeat','02':'jubeat-ripples','03':'jubeat-knit','04':'jubeat-copious-append',
 '05':'jubeat-saucer','06':'jubeat-saucer-fulfill','07':'jubeat-prop','08':'jubeat-qubell',
 '09':'jubeat-clan','10':'jubeat-festo','11':'jubeat-festo','12':'jubeat-ave',
 '13':'jubeat-beyond-ave','14':'jubeat-beyond-ave','15':'jubeat-beyond-ave',
 '16':'jubeat-plus','17':'jukebeat'}
ORDER=['jubeat','jubeat-ripples','jubeat-ripples-append','jubeat-knit','jubeat-copious',
 'jubeat-copious-append','jubeat-saucer','jubeat-saucer-fulfill','jubeat-prop','jubeat-qubell',
 'jubeat-clan','jubeat-festo','jubeat-ave','jubeat-beyond-ave','jubeat-plus','jukebeat','音乐魔方']


def fallback(version):
    return SOURCE_VERSIONS.get(version.split('_')[0], version)


def metadata_plan(directory, web_index, cache, extra=None):
    directory,cache=Path(directory).resolve(),Path(cache).resolve()
    web=load(web_index); groups=defaultdict(list); individual=defaultdict(list); web_by_id={}
    for song in web['songs']:
        groups[count_key(song)].append(song);web_by_id[song['id']]=song
        for c in song['charts']:
            individual[(code(c['code']),c.get('notes',0),c.get('holds',0))].append(song['id'])
    local=[]
    for name in load(directory/'data/library.json')['versionIndexes']:
        local.extend((s,directory,None) for s in load(inside(directory,name))['songs'])
    if extra:
        extra=Path(extra).resolve()
        local.extend((s,extra,'extra') for s in load(extra/'version-index.json')['songs'])
    @lru_cache(maxsize=10000)
    def read(path):return load(Path(path))
    rows=[];used=set();summary=defaultdict(int)
    for number,(song,root,kind) in enumerate(local,1):
        def local_chart(c):
            if kind=='extra':
                prefix='songs/'+song['version']+'/'
                value=song['assets']['chart:'+c['code']]
                if not value.startswith(prefix):raise ValueError('独立版本谱面路径不匹配')
                return inside(root,value[len(prefix):])
            return inside(root,song['assets']['chart:'+c['code']])
        matches=[]
        if sum(c.get('heads',c.get('notes',0)) for c in song['charts'])>=96:
            votes=Counter(wid for c in song['charts'] for wid in individual[(code(c['code']),c.get('notes',0),c.get('holds',0))])
            candidates=[web_by_id[wid] for wid,n in votes.items() if n >= min(2,len(song['charts']))]
            for candidate in candidates:
                evidence={}
                for c in song['charts']:
                    wc=next((w for w in candidate['charts'] if code(w['code'])==code(c['code']) and w.get('notes',0)==c.get('notes',0) and w.get('holds',0)==c.get('holds',0)),None)
                    if wc is None:continue
                    path=candidate.get('assets',{}).get('chart:'+wc['code'])
                    if not path:
                        stem=candidate['id'][:-4] if candidate['id'].endswith('.mcz') else candidate['id']
                        path=f'data/charts/{stem}/{wc["code"]}.json'
                    cached=cache_path(cache,path)
                    if not cached.is_file():raise ValueError('网页谱面缓存不完整: '+path)
                    result=compare(read(str(local_chart(c))),read(str(cached)))
                    if result is None:continue
                    evidence[code(c['code'])]=dict(result,webChart=path,webChartSha256=digest(cached),localChartSha256=digest(local_chart(c)))
                strong=sum(e['heads']>=32 for e in evidence.values())
                if len(evidence)>=min(2,len(song['charts'])) and strong>=min(2,len(song['charts'])):
                    matches.append((candidate,evidence))
        base=fallback(song['version'])
        if kind=='extra':matches=[m for m in matches if m[0]['version']==song['version']]
        if matches:
            most=max(len(m[1]) for m in matches);matches=[m for m in matches if len(m[1])==most]
        same=[m for m in matches if m[0]['version']==base]
        pool=same or matches
        selected=None
        if pool:
            # 同一谱面若在多版本出现，先同原归档版本，否则按街机优先的版本顺序。
            pool.sort(key=lambda m:(ORDER.index(m[0]['version']) if m[0]['version'] in ORDER else 999,
                                   max(e['maxTimingErrorSeconds'] for e in m[1].values()),m[0]['id']))
            first_version=pool[0][0]['version'];first=[m for m in pool if m[0]['version']==first_version]
            signatures={(m[0]['title'],tuple((code(c['code']),str(c.get('level',''))) for c in m[0]['charts'])) for m in first}
            if len(signatures)==1:selected=first[0]
        new=json.loads(json.dumps(song))
        if kind=='extra':
            available={c['code'] for c in new['charts']}
            new['missing']=list(dict.fromkeys(new.get('missing',[])+['charts/'+c for c in ('BSC','ADV','EXT') if c not in available]))
        new['version']=song['version'] if kind=='extra' else (selected[0]['version'] if selected else base)
        if selected:
            target,evidence=selected
            new.update(title=target['title'],titleKnown=True,artist=target.get('artist') or song.get('artist',''))
            for c in new['charts']:
                if code(c['code']) not in evidence or not evidence[code(c['code'])]['heads']:continue
                w=next(x for x in target['charts'] if code(x['code'])==code(c['code']))
                c['level']=str(w.get('level') or '');c['levelKnown']=bool(c['level'])
            new['metadataEvidence']=dict(source='web-json-chart-match',webSong=target['id'],charts=evidence,
                                        coversImported=False,localChartsReplaced=False,unmatchedDifficulties=[c['code'] for c in song['charts'] if code(c['code']) not in evidence])
            summary['matched']+=1
            summary['matched_full' if len(evidence)==len(song['charts']) else 'matched_partial']+=1
        else:summary['unmatched_or_ambiguous']+=1
        tail=song['id'].split('/',1)[1]
        new_id=new['version']+'/'+tail
        if new_id in used:new_id+='__'+song['version']
        if new_id in used:raise ValueError('版本合并后ID冲突')
        used.add(new_id);new['id']=new_id
        for key,value in list(new['assets'].items()):
            prefix='songs/'+song['id']+'/'
            if not value.startswith(prefix):raise ValueError('曲目资源不是自身目录中的显式路径')
            new['assets'][key]='songs/'+new_id+'/'+value[len(prefix):]
        rows.append(dict(old=song['id'],entry=new,extra=kind=='extra',
                         matches=[m[0]['id'] for m in matches],originVersion=song['version']))
        if number%100==0:print('谱面核对',number,'/',len(local),'匹配',summary['matched'],flush=True)
    return dict(format='jubeat-curation-v1',status='complete',webIndexSha256=digest(web_index),
                policy='metadata only; all playable notes/holds/tail directions; no web media; no local chart dedup or replacement',
                summary=dict(summary,packages=len(rows),extra=sum(r['extra'] for r in rows)),rows=rows)


def updated_chart(path, entry, c):
    data=load(path);meta=data.setdefault('meta',{})
    song=meta.setdefault('song',{});song['title']=entry['title'];song['artist']=entry.get('artist','')
    if c.get('levelKnown'):
        meta['level']=c['level'];meta['version']=c['code']+' Lv'+c['level']
    return data


def apply_plan(unified, directory, plan_path, unified_out, directory_out, extra=None):
    unified,directory=Path(unified).resolve(),Path(directory).resolve()
    uout,dout=Path(unified_out).resolve(),Path(directory_out).resolve()
    if uout==dout or uout in dout.parents or dout in uout.parents:
        raise ValueError('两个输出目录必须互相独立')
    for source in (unified,directory):
        for out in (uout,dout):
            if source==out or source in out.parents or out in source.parents:raise ValueError('输入输出目录必须互相独立')
    plan_sha=digest(plan_path)
    for out in (uout,dout):
        marker=out/'.curation-state.json'
        if out.exists() and any(out.iterdir()) and (not marker.is_file() or load(marker).get('planSha256')!=plan_sha):
            raise ValueError('校正输出必须为空或属于同一计划的可续跑目录')
        out.mkdir(parents=True,exist_ok=True)
        save(marker,dict(planSha256=plan_sha,status='running'))
    plan=load(plan_path)
    if plan.get('status')!='complete' or plan.get('format')!='jubeat-curation-v1':raise ValueError('核对计划未完成')
    if extra:extra=Path(extra).resolve()
    original_plan=load(unified/'_reports/生成计划.json');original_index=load(unified/'_reports/歌曲索引.json')
    packages={p['path']:p for p in original_plan['packages']};indexes={p['path']:i for p,i in zip(original_plan['packages'],original_index)}
    mapping={row['old']:row['entry']['id'] for row in plan['rows'] if not row['extra']}
    new_packages=[];new_index=[];by_version={v:[] for v in ORDER};media_verified=0
    for number,row in enumerate(plan['rows'],1):
        entry=row['entry'];old=row['old'];new=entry['id'];target=inside(dout,'songs/'+new);utarget=inside(uout,new)
        if row['extra']:
            if not extra:raise ValueError('计划需要独立版本输入')
            prefix=entry['version']+'/'
            if not old.startswith(prefix):raise ValueError('独立版本路径无效')
            source=inside(extra,old[len(prefix):])
        else:source=inside(unified,old)
        if not row['extra'] and indexes[old].get('cover'):
            if digest(source/'cover.png')!=indexes[old]['cover']['sha256']:
                raise ValueError('本地统一库封面已变化: '+old)
        # 只取明确授权的本地目录，媒体不做任何网页请求或替换。
        for p in source.rglob('*'):
            if p.is_file() and not any(x.startswith('.') for x in p.relative_to(source).parts):
                rel=p.relative_to(source).as_posix();inside(source,rel)
                if rel=='song.json' or (rel.startswith('charts/') and p.suffix=='.mc'):continue
                link_file(p,utarget/rel)
                if rel!='song.json' and not rel.startswith('charts/'):
                    link_file(p,target/rel)
                    if p.suffix.lower() in ('.wav','.png'):media_verified+=1
        for c in entry['charts']:
            compat=source/'charts'/(c['code']+('.json' if row['extra'] else '.mc'))
            if code(c['code']) in entry.get('metadataEvidence',{}).get('charts',{}) and digest(compat)!=entry['metadataEvidence']['charts'][code(c['code'])]['localChartSha256']:
                raise ValueError('核对后本地谱面已变化，拒绝套用元数据: '+old)
            chart=updated_chart(compat,entry,c) if entry.get('metadataEvidence') else load(compat)
            save(utarget/'charts'/(c['code']+'.mc'),chart)
            save(target/'charts'/(c['code']+'.json'),chart)
            exact=source/'charts'/(c['code']+'.events.json')
            link_file(exact,target/'charts'/exact.name)
        if row['extra']:
            audio={}
            for kind in ('bgm','preview'):
                p=source/(kind+'.wav')
                if p.exists():
                    with wave.open(str(p)) as w:audio[kind]=dict(bytes=p.stat().st_size,audio=dict(bits_per_sample=w.getsampwidth()*8,sample_rate=w.getframerate(),channels=w.getnchannels(),frames=w.getnframes()),source=dict(kind='local-independent-version'))
            cover=None
            if (source/'cover.png').exists():
                png=(source/'cover.png').read_bytes();width,height=struct.unpack('>II',png[16:24])
                cover=dict(sha256=digest(source/'cover.png'),bytes=len(png),width=width,height=height,mode='RGBA' if png[25]==6 else 'RGB')
            charts={c['code']:dict(c,notes=c.get('heads',c['notes']-c['holds']),source_format='video_transcription') for c in entry['charts']}
            metadata=dict(schema=1,identity=entry['identity'],title=entry['title'],artist=entry.get('artist',''),
                          source=entry['source'],category=entry['category'],first_path=new,charts=charts,audio=audio,
                          cover=cover,missing=entry['missing'],missing_charts=[c for c in ('BSC','ADV','EXT') if c not in charts])
            package=dict(metadata,path=new)
        else:
            metadata=load(source/'song.json');package=json.loads(json.dumps(packages[old]));package['path']=new
            metadata.update(title=entry['title'] if entry.get('titleKnown') else None,artist=entry.get('artist') or None,
                            first_path=mapping.get(metadata.get('first_path'),metadata.get('first_path')))
            for c in entry['charts']:
                metadata['charts'][c['code']]['level']=c['level'] if c.get('levelKnown') else None
                package['charts'][c['code']]['level']=metadata['charts'][c['code']]['level']
            package.update(title=metadata['title'],artist=metadata['artist'],first_path=mapping.get(package.get('first_path'),package.get('first_path')))
        metadata['version']=entry['version'];package['version']=entry['version']
        for kind, info in metadata.get('audio', {}).items():
            if 'cache' in info:
                info['original_cache']=info['cache'];info['cache']=new+'/'+kind+'.wav'
        if metadata.get('cover') and 'cache' in metadata['cover']:
            metadata['cover']['original_cache']=metadata['cover']['cache'];metadata['cover']['cache']=new+'/cover.png'
        if entry.get('metadataEvidence'):
            metadata['metadataEvidence']=entry['metadataEvidence'];package['metadataEvidence']=entry['metadataEvidence']
        save(utarget/'song.json',metadata);save(target/'song.json',dict(entry,format='jubeat-directory-v1'))
        idx=dict(metadata) if row['extra'] else json.loads(json.dumps(indexes[old]))
        idx.update(title=metadata['title'],artist=metadata['artist'],version=entry['version'],audio=metadata['audio'],cover=metadata['cover'])
        for c in entry['charts']:idx['charts'][c['code']]['level']=c['level'] if c.get('levelKnown') else None
        new_packages.append(package);new_index.append(idx);by_version.setdefault(entry['version'],[]).append(entry)
        if number%100==0:print('整理版本目录',number,'/',len(plan['rows']),flush=True)
    sources=defaultdict(list)
    for p in unified.glob('*/来源索引.json'):
        sources[fallback(p.parent.name)].append(dict(originalVersion=p.parent.name,index=load(p)))
    if extra:sources['音乐魔方'].append(dict(originalVersion='音乐魔方',index=load(extra/'version-index.json')))
    for v,songs in by_version.items():
        source_index=dict(format='jubeat-canonical-sources-v1',version=v,sources=sources[v])
        save(uout/v/'来源索引.json',source_index);save(dout/'data/sources'/(v+'.json'),source_index)
        save(dout/'data/versions'/(v+'.json'),dict(format='jubeat-directory-v1',version=1,versions=[v],songs=songs))
    save(dout/'data/library.json',dict(format='jubeat-directory-v1',version=1,versions=[],songs=[],versionIndexes=['data/versions/'+v+'.json' for v in by_version]))
    stats=dict(format='jubeat-directory-v1',status='complete',packages=len(plan['rows']),
               playablePackages=sum(not r['entry']['resourceOnly'] for r in plan['rows']),
               resourceOnly=sum(r['entry']['resourceOnly'] for r in plan['rows']),
               charts=sum(len(r['entry']['charts']) for r in plan['rows']),
               audio=sum('audio' in r['entry']['assets'] for r in plan['rows']),
               previews=sum('preview' in r['entry']['assets'] for r in plan['rows']),
               covers=sum('cover' in r['entry']['assets'] for r in plan['rows']),
               versions={v:len(s) for v,s in by_version.items()},metadataMatched=plan['summary']['matched'],
               webMediaImported=False,policy='本地WAV/PNG原样；精确事件保留各自tick_hz；音乐魔方为视频抄谱独立版本')
    save(dout/'data/export.json',stats)
    for p in (unified/'_reports').rglob('*.json'):
        dest=uout/'_reports/归档生成报告'/p.relative_to(unified/'_reports');link_file(p,dest)
    old_summary=original_plan.get('summary',{})
    original_plan['summary']=dict(old_summary,originalSourceSummary=old_summary.copy(),
        identities=len({r['entry']['identity'] for r in plan['rows'] if not r['entry']['resourceOnly']}),
        packages=len(new_packages),playable_packages=stats['playablePackages'],
        incomplete_packages=stats['resourceOnly'],observations=old_summary.get('observations',0)+plan['summary'].get('extra',0))
    original_plan['packages']=new_packages;original_plan['curation']=dict(planSha256=digest(plan_path),canonicalVersions=True,webMediaImported=False)
    save(uout/'_reports/生成计划.json',original_plan);save(uout/'_reports/歌曲索引.json',new_index)
    save(uout/'_reports/生成结果.json',dict(status='complete',packages=len(new_packages),curated=True,sourceStats=stats))
    save(uout/'_reports/目录与元数据校正.json',plan);save(dout/'data/curation.json',dict(summary=plan['summary'],webMediaImported=False))
    save(uout/'_reports/浏览器导出统计.json',stats)
    for out in (uout,dout):save(out/'.curation-state.json',dict(planSha256=plan_sha,status='complete'))
    return stats


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',type=Path,required=True);p.add_argument('--web-index',type=Path,required=True)
    p.add_argument('--cache',type=Path,required=True);p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--extra',type=Path);p.add_argument('--unified',type=Path)
    p.add_argument('--unified-out',type=Path);p.add_argument('--directory-out',type=Path)
    p.add_argument('--apply',action='store_true');a=p.parse_args()
    if a.apply:
        if not all((a.unified,a.unified_out,a.directory_out)):p.error('--apply需要三个统一库/输出路径参数')
        stats=apply_plan(a.unified,a.directory,a.plan,a.unified_out,a.directory_out,a.extra);print(json.dumps(stats,ensure_ascii=False))
    else:save(a.plan,metadata_plan(a.directory,a.web_index,a.cache,a.extra))
if __name__=='__main__':main()
