from pathlib import Path
import json,tempfile,unittest
from analysis.summary import assess

class SummaryCountTests(unittest.TestCase):
    def test_standalone_tex_added_once_to_middle_texture_count(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);v=root/'07_prop';v.mkdir()
            values={'stats.json':{'resource_classes':{'song_complete':1},'songs_complete':1,'ifs_total':1,'bgm':1,'preview':1,'charts':3,'charts_with_notes':3,'errors':0},'manifest.json':{'songs':[]},'extras-stats.json':{'texture_png':100,'errors':[]},'individual-texture-manifest.json':{'stats':{'png_decoded':7},'records':[],'errors':[]},'errors.json':[]}
            for name,value in values.items():(v/name).write_text(json.dumps(value))
            r=assess(root,('07','prop','release','middle'),v,True)
            self.assertEqual(r['extra_assets']['png_files'],107)
            self.assertEqual(r['extra_assets']['individual_texture_png_files'],7)
            self.assertIn('excludes duplicate cover links',r['extra_assets']['png_count_scope'])
if __name__=='__main__':unittest.main()
