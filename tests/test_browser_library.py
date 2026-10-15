"""Pure synthetic unified-library input and ZIP output, without game resources."""
from pathlib import Path
import hashlib
import json
import tempfile
import unittest
import wave
import zipfile
from workflows.browser_library import export_library, pack_versions, save, load, relative, inside


def fixture(root):
    packages=[];index=[]
    for number, charts in ((1,True),(2,False)):
        key=f'01_synthetic/首次收录/arcade_{number}'
        source=dict(id=str(number),version='01_synthetic')
        package=dict(path=key,identity=f'arcade:{number}',source=source,category='首次收录',title=None)
        directory=root/key;directory.mkdir(parents=True)
        audio={}
        for kind in ('bgm','preview') if charts else ('bgm',):
            path=directory/(kind+'.wav')
            with wave.open(str(path),'wb') as wav:
                wav.setparams((1,3,24000,0,'NONE','not compressed'));wav.writeframes(b'\x01\x02\x03'*32)
            audio[kind]=dict(bytes=path.stat().st_size)
        metadata={}
        if charts:
            save(directory/'charts/BSC.mc',dict(meta={},time=[dict(beat=[0,0,1],bpm=120)],note=[dict(beat=[0,1,48],index=0)]))
            save(directory/'charts/BSC.events.json',dict(events=[dict(tick=1,command='PLAY',value=0)]))
            (directory/'original').mkdir();(directory/'original/BSC.eve').write_text('1,PLAY,0\n')
            metadata['BSC']=dict(notes=1,holds=1,level=None,playable=True)
        save(directory/'provenance.json',dict(synthetic=True))
        packages.append(package)
        index.append(dict(identity=package['identity'],source=source,audio=audio,charts=metadata,
                          cover=None,missing=['cover','chart:ADV','chart:EXT']+([] if charts else ['chart:BSC','preview'])))
    save(root/'_reports/生成计划.json',dict(packages=packages))
    save(root/'_reports/生成结果.json',dict(status='complete'))
    save(root/'_reports/歌曲索引.json',index)
    save(root/'01_synthetic/来源索引.json',dict(observations=[1,2]))
    save(root/'02_zero/来源索引.json',dict(observations=[]))
    (root/'ignore-old.mcz').write_bytes(b'must-not-open')
    return packages


class BrowserLibraryTests(unittest.TestCase):
    def test_directory_counts_precision_original_preview_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'unified';out=Path(tmp)/'browser';packages=fixture(root)
            rows=export_library(root,out)
            self.assertEqual(len(rows['01_synthetic']),2);self.assertEqual(rows['02_zero'],[])
            charted,resource=rows['01_synthetic']
            self.assertEqual(charted['charts'][0]['notes'],2)
            self.assertEqual(charted['charts'][0]['level'],'');self.assertFalse(charted['titleKnown'])
            self.assertTrue(resource['resourceOnly']);self.assertNotIn('preview',resource['assets'])
            self.assertIn('chart:BSC',resource['missing'])
            for kind in ('bgm','preview'):
                self.assertEqual((out/'songs'/packages[0]['path']/(kind+'.wav')).read_bytes(),
                                 (root/packages[0]['path']/(kind+'.wav')).read_bytes())
            self.assertEqual(load(out/charted['assets']['exact:BSC'])['events'][0]['tick'],1)
            self.assertEqual(load(out/charted['assets']['chart:BSC']),load(root/packages[0]['path']/'charts/BSC.mc'))
            self.assertFalse((out/'ignore-old.mcz').exists())
            self.assertEqual(load(out/'data/export.json')['resourceOnly'],1)
            export_library(root,out)  # Idempotent hardlinks.

    def test_zip64_manifest_hash_crc_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'unified';out=Path(tmp)/'browser';fixture(root);export_library(root,out)
            archives=Path(tmp)/'archives';rows=pack_versions(out,archives,verify=True)
            self.assertEqual(len(rows),2)
            for row in rows:
                path=archives/(row['version']+'.zip')
                self.assertTrue(row['crc_verified'])
                self.assertEqual(row['zip_sha256'],hashlib.sha256(path.read_bytes()).hexdigest())
                with zipfile.ZipFile(path) as z:
                    self.assertIsNone(z.testzip())
                    for item in json.loads(z.read('PACKAGE.json'))['files']:
                        self.assertEqual(item['sha256'],hashlib.sha256(z.read(item['path'])).hexdigest())
                        self.assertFalse(item['path'].endswith('.mcz'))
            mtimes=[(archives/(r['version']+'.zip')).stat().st_mtime_ns for r in rows]
            pack_versions(out,archives,verify=True)
            self.assertEqual(mtimes,[(archives/(r['version']+'.zip')).stat().st_mtime_ns for r in rows])

    def test_paths_and_incomplete_source_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'unified';fixture(root)
            for value in ('../x','a/../b','/absolute','a\\b','a//b','a:bad'):
                with self.assertRaises(ValueError):relative(value)
            (root/'escape').symlink_to(Path(tmp),target_is_directory=True)
            with self.assertRaises(ValueError):inside(root,'escape/x')
            with self.assertRaises(ValueError):export_library(root,root/'nested')
            save(root/'_reports/生成结果.json',dict(status='incomplete'))
            with self.assertRaises(ValueError):export_library(root,Path(tmp)/'out')

    def test_pack_rejects_output_inside_library_and_symlink_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'unified';out=Path(tmp)/'browser';packages=fixture(root);export_library(root,out)
            with self.assertRaises(ValueError):pack_versions(out,out/'archives')
            outside=Path(tmp)/'secret.txt';outside.write_text('synthetic outside file')
            (out/'songs'/packages[0]['path']/'escaped.txt').symlink_to(outside)
            with self.assertRaises(ValueError):pack_versions(out,Path(tmp)/'archives')

    def test_changed_cover_or_audio_does_not_silently_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'unified';packages=fixture(root)
            (root/packages[0]['path']/'bgm.wav').write_bytes(b'broken')
            with self.assertRaises(ValueError):export_library(root,Path(tmp)/'out')

if __name__=='__main__':unittest.main()
