"""Artificial T44 data trees; no original game media or real song identifiers."""
from pathlib import Path
import contextlib
import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflows import t44


def fixture(root, header=True):
    source = root / 'source'
    (source / 'music/00000001').mkdir(parents=True)
    xml = '<root>' + ('<header/>' if header else '') + '<body><data><music_id>00000001</music_id><music_name>Synthetic</music_name><level_bsc>10</level_bsc></data></body></root>'
    (source / 'music/music_info.xml').write_text(xml)
    (source / 'music/00000001/bsc.eve').write_text('0,TEMPO,500000\n0,PLAY,0\n10,HAKU,42\n300,END,0\n')
    (source / 'unknown/nested').mkdir(parents=True)
    (source / 'unknown/nested/opaque.dat').write_bytes(b'SYNTHETIC UNKNOWN BYTES')
    return source


def bank(members):
    offset = 8 + 8 * len(members)
    table = []
    for member in members:
        table.append(struct.pack('<II', offset, len(member)))
        offset += len(member)
    return b'S3P0' + struct.pack('<I', len(members)) + b''.join(table) + b''.join(members)


def fake_run(args, **kwargs):
    if '-of' in args:
        return subprocess.CompletedProcess(args, 0, json.dumps({'streams': [{'codec_type': 'audio', 'codec_name': 'flac'}]}).encode(), b'')
    Path(args[-1]).write_bytes(b'SYNTHETIC CONVERTED BYTES')
    return subprocess.CompletedProcess(args, 0, b'', b'')


class T44Tests(unittest.TestCase):
    def export(self, source, out):
        with patch.object(t44.shutil, 'which', side_effect=lambda name: name), contextlib.redirect_stdout(io.StringIO()):
            return t44.T44Exporter(source, out).run()

    def test_raw_snapshot_and_exact_guide_values(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, out = fixture(root, header=False), root / 'out'
            code = self.export(source, out)
            self.assertEqual(code, 0)
            for path in source.rglob('*'):
                if path.is_file():
                    self.assertEqual(path.read_bytes(), (out / 'raw' / path.relative_to(source)).read_bytes())
            events = json.loads((out / 'songs/00000001/charts/bsc.events.json').read_text())
            self.assertEqual(events['events'][2]['value'], 42)
            self.assertEqual(events['events'][2]['values'], ['42'])
            self.assertIsNone(json.loads((out / 'extras/unique-content.json').read_text())['header'])
            chart = json.loads((out / 'songs/00000001/charts/bsc.mc').read_text())
            self.assertFalse(any('sound' in n for n in chart['note']))
            self.assertEqual(chart['meta']['cover'], '')
            self.assertTrue(json.loads((out / 'stats.json').read_text())['finished'])

    def test_exact_fields_unknown_command_and_rejected_line(self):
        raw = b'0,TEMPO,500000\r\n 10 , HAKU , 77 , tail \r\n20,UNKNOWN,text,2\nmalformed\n\n'
        parsed = t44.exact_eve_events(raw)
        self.assertEqual(parsed['source_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(parsed['events'][1]['fields'], [' 10 ', ' HAKU ', ' 77 ', ' tail '])
        self.assertEqual(parsed['events'][1]['values'], ['77', 'tail'])
        self.assertEqual(parsed['events'][2]['value'], 'text')
        self.assertEqual(parsed['unknown_commands'], ['UNKNOWN'])
        self.assertEqual(parsed['unparsed_lines'][0]['raw_line'], 'malformed')
        self.assertEqual(len(parsed['lines']), 5)

    def test_bad_eve_still_writes_exact_json_and_final_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, out = fixture(root), root / 'out'
            (source / 'music/00000001/bsc.eve').write_text('invalid row\n')
            self.assertEqual(self.export(source, out), 2)
            parsed = json.loads((out / 'songs/00000001/charts/bsc.events.json').read_text())
            self.assertEqual(len(parsed['unparsed_lines']), 1)
            self.assertTrue((out / 'songs/00000001/original/bsc.eve').exists())
            self.assertTrue(json.loads((out / 'stats.json').read_text())['finished'])

    def test_metadata_escape_and_mismatch_are_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, out = fixture(root), root / 'out'
            sentinel = root / 'outside'; sentinel.write_bytes(b'SYNTHETIC OUTSIDE')
            records = [{'path': '../outside', 'size': sentinel.stat().st_size, 'sha1': hashlib.sha1(sentinel.read_bytes()).hexdigest()},
                       {'path': 'missing', 'size': 1, 'sha1': 'bad'},
                       {'path': '/music/00000001/bsc.eve', 'size': 999, 'sha1': 'bad'}]
            (source / '__metadata.metatxt').write_text(json.dumps({'files': records}))
            self.assertEqual(self.export(source, out), 2)
            checks = json.loads((out / 'extras/embedded-metadata-verification.json').read_text())
            self.assertFalse(checks[0]['exists'])
            self.assertIn('Unsafe', checks[0]['error'])
            self.assertEqual(len(json.loads((out / 'errors.json').read_text())), 3)
            self.assertEqual(sentinel.read_bytes(), b'SYNTHETIC OUTSIDE')

    def test_video_probe_failure_does_not_abort_reports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, out = fixture(root), root / 'out'
            (source / 'music/00000001/broken.wmv').write_bytes(b'SYNTHETIC BROKEN VIDEO')
            with patch.object(t44.T44Exporter, 'probe', side_effect=ValueError('synthetic video probe failure')):
                self.assertEqual(self.export(source, out), 2)
            errors = json.loads((out / 'errors.json').read_text())
            self.assertEqual(errors[0]['stage'], 'video')
            self.assertTrue((out / 'raw/music/00000001/broken.wmv').exists())
            self.assertTrue((out / 'manifest.json').exists())
            self.assertTrue((out / 'stats.json').exists())

    def test_s3p_bad_member_continues_with_complete_wrappers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, out = fixture(root), root / 'out'
            wrapped = b'S3V0' + struct.pack('<II', 12, 4) + b'FAKE'
            original = bank([b'BAD!' + bytes(12), wrapped])
            (source / 'system.s3p').write_bytes(original)
            with patch.object(t44.subprocess, 'run', side_effect=fake_run):
                self.assertEqual(self.export(source, out), 2)
            self.assertEqual((out / 'raw/system.s3p').read_bytes(), original)
            self.assertEqual((out / 'extras/sound/system/0001.s3v').read_bytes(), wrapped)
            self.assertEqual((out / 'extras/sound/system/0001.asf').read_bytes(), b'FAKE')
            records = json.loads((out / 'extras/sound-results.json').read_text())
            self.assertEqual([r['index'] for r in records], [0, 1])
            self.assertEqual(records[1]['status'], 'decoded')
            self.assertEqual(json.loads((out / 'stats.json').read_text())['sound_bank_audio_decoded'], 1)

    def test_ifs_paths_checked_before_converter_can_write(self):
        for names in (['../escape'], ['/absolute'], ['Name', 'name'], ['ifs_manifest.xml']):
            state = {'extract_called': False, 'closed': False}
            class FakeIFS:
                tree = SimpleNamespace(all_files=[SimpleNamespace(full_path=name) for name in names], all_folders=[])
                def extract(self, **kwargs):
                    state['extract_called'] = True
                def close(self):
                    state['closed'] = True
            fake_module = SimpleNamespace(IFS=lambda _: FakeIFS())
            with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, {'ifstools': fake_module}):
                with self.assertRaises(ValueError):
                    t44.checked_ifs_extract(Path(temp) / 'synthetic.ifs', Path(temp) / 'out')
            self.assertFalse(state['extract_called'])
            self.assertTrue(state['closed'])

    def test_copy_fallback_and_unsafe_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, out = fixture(root), root / 'out'
            with patch.object(t44.os, 'link', side_effect=OSError('synthetic cross-device link')):
                self.assertEqual(self.export(source, out), 0)
            self.assertEqual((out / 'raw/unknown/nested/opaque.dat').read_bytes(), b'SYNTHETIC UNKNOWN BYTES')
            with self.assertRaisesRegex(ValueError, 'new output'):
                self.export(source, out)
            with self.assertRaisesRegex(ValueError, 'non-nested'):
                self.export(source, source / 'nested-output')
            linked = root / 'output-link'; linked.symlink_to(root / 'not-created')
            with self.assertRaisesRegex(ValueError, 'symbolic'):
                self.export(source, linked)
            (source / 'unsafe-link').symlink_to(root / 'outside')
            with self.assertRaisesRegex(ValueError, 'symbolic'):
                self.export(source, root / 'another-output')


if __name__ == '__main__':
    unittest.main()
