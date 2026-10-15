"""Category deletion uses synthetic temporary files; ordinary partial songs stay."""
from pathlib import Path
import tempfile
import unittest
from workflows.browser_library import export_library,load,save
from workflows.prune_library import prune_unified,prune_directory,CATEGORY
from test_browser_library import fixture
class PruneTests(unittest.TestCase):
    def test_preview_then_delete_updates_both_indexes_and_preserves_charted_song(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'u';fixture(root)
            plan=load(root/'_reports/生成计划.json');p=plan['packages'][1];old=p['path'];p['category']=CATEGORY
            p['path']=old.replace('/首次收录/','/'+CATEGORY+'/');dest=root/p['path'];dest.parent.mkdir(parents=True);(root/old).rename(dest)
            save(root/'_reports/生成计划.json',plan)
            browser=Path(tmp)/'browser';export_library(root,browser)
            self.assertEqual(prune_unified(root)['removed'],1);self.assertTrue(dest.exists())
            self.assertEqual(prune_directory(browser)['removed'],1)
            self.assertEqual(prune_unified(root,True)['remaining'],1)
            self.assertEqual(prune_directory(browser,True)['remaining'],1)
            self.assertFalse(dest.exists());self.assertFalse((browser/'songs'/p['path']).exists())
            normal=plan['packages'][0]['path']
            self.assertTrue((root/normal/'bgm.wav').exists());self.assertTrue((browser/'songs'/normal/'charts/BSC.json').exists())
            self.assertEqual(load(browser/'data/export.json')['charts'],1)
            self.assertEqual(load(browser/'data/export.json')['resourceOnly'],0)
            prune_unified(root,True);prune_directory(browser,True)
            self.assertEqual(load(root/'_reports/已移除特殊资源.json')['count'],1)
            self.assertEqual(load(browser/'data/pruned.json')['count'],1)
            self.assertEqual(load(browser/'data/export.json')['removedSpecial'],1)
if __name__=='__main__':unittest.main()
