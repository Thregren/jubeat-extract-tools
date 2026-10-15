#!/usr/bin/env python3
"""按分类删除已生成库中的条目与文件，并更新索引。默认只预览，--apply执行。"""
from __future__ import annotations
import argparse
from pathlib import Path
import shutil
from workflows.browser_library import load, save, inside
CATEGORY='特殊或不完整资源'


def prune_unified(root, apply=False):
    root=Path(root).resolve();plan=load(root/'_reports/生成计划.json');index=load(root/'_reports/歌曲索引.json')
    if len(plan['packages'])!=len(index):raise ValueError('计划与索引数量不符')
    pairs=list(zip(plan['packages'],index));removed=[p for p,_ in pairs if p.get('category')==CATEGORY]
    kept=[(p,i) for p,i in pairs if p.get('category')!=CATEGORY]
    if apply:
        if removed:save(root/'_reports/已移除特殊资源.json',dict(category=CATEGORY,removed=[dict(path=p['path'],identity=p['identity']) for p in removed],count=len(removed)))
        for version in root.iterdir():
            folder=version/CATEGORY
            if version.is_dir() and not version.name.startswith('_') and folder.is_dir():
                inside(root,folder.relative_to(root).as_posix());shutil.rmtree(folder)
        plan['packages']=[p for p,_ in kept]
        summary=plan.setdefault('summary',{})
        playable=sum(any(c.get('playable') for c in i.get('charts',{}).values()) for _,i in kept)
        summary.update(packages=len(kept),playable_packages=playable,incomplete_packages=len(kept)-playable,removed_special_packages=summary.get('removed_special_packages',0)+len(removed))
        plan['excluded_category']=CATEGORY
        save(root/'_reports/生成计划.json',plan);save(root/'_reports/歌曲索引.json',[i for _,i in kept])
        result=load(root/'_reports/生成结果.json');result.update(status='complete',packages=len(kept),removedSpecial=result.get('removedSpecial',0)+len(removed))
        stats=result.get('sourceStats',{})
        if stats:
            versions={v:0 for v in stats.get('versions',{})}
            for p,_ in kept:
                v=p['path'].split('/')[0];versions[v]=versions.get(v,0)+1
            stats.update(packages=len(kept),playablePackages=playable,resourceOnly=len(kept)-playable,
                charts=sum(len(i.get('charts',{})) for _,i in kept),
                audio=sum('bgm' in i.get('audio',{}) for _,i in kept),previews=sum('preview' in i.get('audio',{}) for _,i in kept),
                covers=sum(bool(i.get('cover')) for _,i in kept),versions=versions,removedSpecial=result['removedSpecial'],excludedCategory=CATEGORY)
            save(root/'_reports/浏览器导出统计.json',stats)
        save(root/'_reports/生成结果.json',result)
    return dict(rootKind='unified',removed=len(removed),remaining=len(kept))


def prune_directory(root, apply=False):
    root=Path(root).resolve();main=load(root/'data/library.json');removed=[];songs=[]
    if main.get('format')!='jubeat-directory-v1' or not isinstance(main.get('versionIndexes'),list):
        raise ValueError('只支持有versionIndexes的开放目录库')
    catalogs=[]
    for name in main.get('versionIndexes',[]):
        path=inside(root,name)
        if not path.is_file():continue
        catalog=load(path);gone=[s for s in catalog['songs'] if s.get('category')==CATEGORY]
        catalog['songs']=[s for s in catalog['songs'] if s.get('category')!=CATEGORY]
        removed.extend(gone);songs.extend(catalog['songs']);catalogs.append((path,catalog))
    if apply:
        for version in (root/'songs').iterdir():
            folder=version/CATEGORY
            if version.is_dir() and folder.is_dir():
                inside(root,folder.relative_to(root).as_posix());shutil.rmtree(folder)
        for path,catalog in catalogs:save(path,catalog)
        stats=load(root/'data/export.json') if (root/'data/export.json').exists() else {}
        stats.update(status='complete',packages=len(songs),playablePackages=sum(not s.get('resourceOnly',False) for s in songs),
            resourceOnly=sum(bool(s.get('resourceOnly')) for s in songs),charts=sum(len(s['charts']) for s in songs),
            audio=sum('audio' in s['assets'] for s in songs),previews=sum('preview' in s['assets'] for s in songs),
            covers=sum('cover' in s['assets'] for s in songs),versions={c['versions'][0]:len(c['songs']) for _,c in catalogs},
            removedSpecial=stats.get('removedSpecial',0)+len(removed),excludedCategory=CATEGORY)
        save(root/'data/export.json',stats)
        if removed:save(root/'data/pruned.json',dict(category=CATEGORY,count=len(removed),removed=[s['id'] for s in removed]))
    return dict(rootKind='directory',removed=len(removed),remaining=len(songs))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--unified',type=Path,action='append',default=[])
    p.add_argument('--directory',type=Path,action='append',default=[]);p.add_argument('--apply',action='store_true');a=p.parse_args()
    if not a.unified and not a.directory:p.error('至少指定一种库目录')
    for root in a.unified:print(prune_unified(root,a.apply),flush=True)
    for root in a.directory:print(prune_directory(root,a.apply),flush=True)
if __name__=='__main__':main()
