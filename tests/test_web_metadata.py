"""Synthetic JSON-only metadata matches; no live service, music or cover downloads."""
from pathlib import Path
import json
import tempfile
import unittest
from workflows.browser_library import export_library, load, save, digest
from workflows.web_metadata import compare, chart_paths, cache_path
from workflows.curate_library import metadata_plan, apply_plan
from test_browser_library import fixture


def chart(panel=0):
    return dict(meta={'song':{'title':'unknown'},'level':'?'},time=[dict(beat=[0,0,1],bpm=120)],
                note=[dict(index=(i+panel)%16,beat=[i,0,1]) for i in range(64)])


def sources(base):
    unified=base/'unified';packages=fixture(unified)
    plan=load(unified/'_reports/生成计划.json');idx=load(unified/'_reports/歌曲索引.json')
    for n,p in enumerate(plan['packages']):
        if n==0:
            idx[n]['charts']={code:dict(notes=64,holds=0,level=None,playable=True) for code in ('BSC','ADV','EXT')}
            for code in idx[n]['charts']:
                save(unified/p['path']/'charts'/(code+'.mc'),chart())
                save(unified/p['path']/'charts'/(code+'.events.json'),dict(tick_hz=300,events=[]))
        p['charts']=idx[n]['charts'];p['first_path']=p['path'];p['artist']=None
        save(unified/p['path']/'song.json',dict(idx[n],title=None,artist=None,first_path=p['path'],category=p['category']))
    save(unified/'_reports/生成计划.json',plan);save(unified/'_reports/歌曲索引.json',idx)
    directory=base/'browser';export_library(unified,directory)
    web=dict(songs=[dict(id='jubeat-ripples-append/synthetic.mcz',version='jubeat-ripples-append',title='Synthetic title',artist='Synthetic artist',
        charts=[dict(code=c,notes=64,holds=0,level=str(i+2)) for i,c in enumerate(('BAS','ADV','EXT'))],
        assets={**{'chart:'+c:'data/charts/synthetic/'+c+'.json' for c in ('BAS','ADV','EXT')},'cover':'media/cover/never-import.png'})])
    webfile=base/'web.json';save(webfile,web);cache=base/'cache'
    for c in ('BAS','ADV','EXT'):save(cache_path(cache,'data/charts/synthetic/'+c+'.json'),chart())
    return unified,directory,webfile,cache,packages


class WebMetadataTests(unittest.TestCase):
    def test_paths_never_include_web_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,_,web,_,_=sources(Path(tmp))
            paths=chart_paths(load(web));self.assertEqual(len(paths),3)
            self.assertTrue(all(p.startswith('data/charts/') for p in paths))
            bad=load(web);bad['songs'][0]['assets']['chart:EXT']='media/cover/x.png'
            with self.assertRaises(ValueError):chart_paths(bad)

    def test_panel_timing_and_hold_direction_must_match(self):
        a=chart();self.assertIsNotNone(compare(a,chart()))
        self.assertIsNone(compare(a,chart(1)))
        b=chart();b['note'][30]['beat']=[30,1,2];self.assertIsNone(compare(a,b))
        a['note'][0].update(endbeat=[1,0,1],endindex=4)
        b=json.loads(json.dumps(a));b['note'][0]['endindex']=8
        self.assertIsNone(compare(a,b))

    def test_partial_levels_only_apply_to_matching_difficulties(self):
        with tempfile.TemporaryDirectory() as tmp:
            unified,directory,web,cache,packages=sources(Path(tmp))
            save(cache_path(cache,'data/charts/synthetic/EXT.json'),chart(1))
            plan=metadata_plan(directory,web,cache)
            entry=plan['rows'][0]['entry']
            self.assertEqual(entry['title'],'Synthetic title')
            self.assertEqual(plan['summary']['matched_partial'],1)
            self.assertEqual(entry['metadataEvidence']['unmatchedDifficulties'],['EXT'])
            self.assertEqual(entry['charts'][0]['level'],'2');self.assertFalse(entry['charts'][2]['levelKnown'])

    def test_new_trees_rename_versions_without_changing_media_or_playable_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);unified,directory,web,cache,packages=sources(base)
            plan=metadata_plan(directory,web,cache);planfile=base/'plan.json';save(planfile,plan)
            original=unified/packages[0]['path']/'charts/BSC.mc';original_hash=digest(original)
            stats=apply_plan(unified,directory,planfile,base/'new-unified',base/'new-browser')
            new=plan['rows'][0]['entry']['id']
            self.assertTrue(new.startswith('jubeat-ripples-append/'))
            changed=load(base/'new-unified'/new/'charts/BSC.mc')
            self.assertEqual(changed['note'],load(original)['note']);self.assertEqual(changed['time'],load(original)['time'])
            self.assertEqual(changed['meta']['song']['title'],'Synthetic title')
            self.assertEqual(digest(original),original_hash)
            self.assertTrue((base/'new-unified'/new/'bgm.wav').samefile(unified/packages[0]['path']/'bgm.wav'))
            self.assertFalse(stats['webMediaImported']);self.assertEqual(stats['packages'],2)

    def test_independent_version_format_and_resume(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);unified,directory,web,cache,packages=sources(base)
            entry=load(directory/'data/versions/01_synthetic.json')['songs'][0]
            old_id=entry['id'];entry.update(format='jubeat-directory-v1',id='音乐魔方/SYNTHETIC',version='音乐魔方',title='Local reviewed title',titleKnown=True)
            entry['assets']={k:v.replace('songs/'+old_id+'/', 'songs/'+entry['id']+'/') for k,v in entry['assets'].items()}
            extra=base/'extra';shutil.copytree(directory/'songs'/old_id,extra/'SYNTHETIC')
            save(extra/'version-index.json',dict(format='jubeat-directory-v1',version='音乐魔方',songs=[entry]))
            planfile=base/'plan.json';save(planfile,metadata_plan(directory,web,cache,extra))
            for _ in range(2):
                stats=apply_plan(unified,directory,planfile,base/'new-u',base/'new-b',extra)
                self.assertEqual(stats['packages'],3)
            self.assertTrue((base/'new-b/songs/音乐魔方/SYNTHETIC/bgm.wav').samefile(extra/'SYNTHETIC/bgm.wav'))
            self.assertEqual(load(base/'new-b/data/versions/音乐魔方.json')['songs'][0]['title'],'Local reviewed title')

    def test_stale_local_chart_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);unified,directory,web,cache,packages=sources(base)
            planfile=base/'plan.json';save(planfile,metadata_plan(directory,web,cache))
            save(unified/packages[0]['path']/'charts/BSC.mc',chart(1))
            with self.assertRaisesRegex(ValueError,'本地谱面已变化'):
                apply_plan(unified,directory,planfile,base/'new-u',base/'new-b')

if __name__=='__main__':unittest.main()
