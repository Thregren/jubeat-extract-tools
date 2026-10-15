"""Synthetic fixtures only: no game binaries, real song lists or source assets."""
from pathlib import Path
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from legacy.pkfs import (ENTRY, XorCodec, candidate_index, crc16_x25, decode_payload,
                         parse_index, path_hashes, xor_python)
from legacy.export import (chart_events, export_layers, extract_pak, music_role,
                           safe_relative)

SYNTHETIC_ID = '00000042'
EVE = b'0,MEASURE,0\n0,TEMPO,500000\n1,PLAY,3\n600,END,0\n'


def independent_xor(data, crc, subkey, historical=False):
    """Bytewise reference; deliberately separate from the word-oriented Python codec."""
    out = bytearray(data)
    key = (crc + subkey) & 0xffffffff
    key = ((key << 11) | (key >> 21)) & 0xffffffff
    at = 0
    while at + 4 <= len(out):
        for byte in range(4):
            out[at + byte] ^= key.to_bytes(4, 'little')[byte]
        key = (key + subkey) & 0xffffffff
        key = ((key << 11) | (key >> 21)) & 0xffffffff
        at += 4
    for byte in range(len(out) - at):
        out[at if historical else at + byte] ^= key.to_bytes(4, 'little')[byte]
    return bytes(out)


def fixture(root, items):
    """items=(virtual name, plaintext, transform). Build small pack 0 fixtures."""
    root.mkdir(parents=True, exist_ok=True)
    package = bytearray()
    table = bytearray()
    for name, plain, transform in items:
        crc, sub = path_hashes(name)
        cipher = independent_xor(plain, crc, sub, transform == 'historical') if transform != 'verbatim' else plain
        table += ENTRY.pack(hashlib.md5(plain).digest(), crc, sub, 0, len(package), len(cipher))
        package += cipher
    header = bytearray(16)
    struct.pack_into('<I', header, 8, len(table))
    (root / 'packinfo.bin').write_bytes(header + table)
    (root / 'd000').mkdir(exist_ok=True)
    (root / 'd000/pack0000.pak').write_bytes(package)


class PKFSCodecTests(unittest.TestCase):
    def test_standard_crc_vectors(self):
        self.assertEqual(crc16_x25(b'123456789'), 0x906e)
        self.assertEqual(path_hashes('123456789'), (0xcbf43926, 0x906e))

    def test_reference_and_all_tail_lengths(self):
        for length in range(40):
            plain = bytes(range(length))
            for historical in [False, True]:
                ref = independent_xor(plain, 0xf1234567, 0xffee, historical)
                self.assertEqual(xor_python(plain, 0xf1234567, 0xffee, historical), ref)
                self.assertEqual(xor_python(ref, 0xf1234567, 0xffee, historical), plain)

    def test_native_matches_reference(self):
        try:
            codec = XorCodec('required')
        except RuntimeError as exc:
            self.skipTest('Optional C compiler/runtime unavailable: ' + str(exc))
        for length in range(40):
            for historical in [False, True]:
                plain = bytes(range(length))
                self.assertEqual(codec(plain, 0xf1234567, 0xffee, historical),
                                 independent_xor(plain, 0xf1234567, 0xffee, historical))

    def test_md5_gates_every_transform(self):
        for remainder in [2, 3]:
            plain = b'payload' + bytes(remainder + 1)
            while len(plain) % 4 != remainder:
                plain += b'x'
            crc, sub = path_hashes('/data/example.bin')
            row = {'crc32': crc, 'crc16': sub, 'stored_md5': hashlib.md5(plain).hexdigest()}
            cipher = independent_xor(plain, crc, sub, True)
            restored, mode, ok = decode_payload(cipher, row)
            self.assertTrue(ok)
            self.assertEqual(restored, plain)
            self.assertEqual(mode, 'pkfs_rolling_xor_historical_tail')
            row['stored_md5'] = '00' * 16
            self.assertEqual(decode_payload(cipher, row), (cipher, 'stored_unverified', False))

    def test_index_bounds(self):
        for blob in [b'', bytes(15), bytes(8) + struct.pack('<I', 1) + bytes(5),
                     bytes(8) + struct.pack('<I', 32) + bytes(4)]:
            with self.assertRaises(ValueError):
                parse_index(blob)
        self.assertEqual(parse_index(bytes(16)), [])

    def test_candidate_double_hash(self):
        index = candidate_index(['/data/example.bin', '/data/example.bin'])
        crc, sub = path_hashes('/data/example.bin')
        self.assertEqual(index[(crc, sub)], ['/data/example.bin'])
        self.assertNotIn((crc, sub ^ 1), index)


class PAKExportTests(unittest.TestCase):
    def test_plain_xor_historical_names_and_rerun(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, output = root / 'input', root / 'output'
            entries = [('/data/one.bin', b'first plaintext', 'verbatim'),
                       ('/append/data/two.bin', b'legacy tail 123', 'historical'),
                       ('/data/three.bin', EVE, 'xor')]
            fixture(source, entries)
            original = (source / 'd000/pack0000.pak').read_bytes()
            report = extract_pak(source, output, [x[0] for x in entries], native='off')
            self.assertEqual(report['stats']['errors'], 0)
            self.assertEqual(report['stats']['md5_verified'], 3)
            self.assertEqual(report['stats']['recovered_names'], 3)
            for record, (_, plain, _) in zip(report['records'], entries):
                self.assertEqual((output / record['path']).read_bytes(), plain)
            self.assertEqual((output / 'raw/d000/pack0000.pak').read_bytes(), original)
            self.assertEqual((source / 'd000/pack0000.pak').read_bytes(), original)
            rerun = extract_pak(source, output, [x[0] for x in entries], native='off')
            self.assertEqual(rerun['stats'], report['stats'])

    def test_unresolved_names_and_md5_failure_preserve_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'input'; output = root / 'output'
            fixture(source, [('/data/example.bin', b'known plaintext', 'xor')])
            index = bytearray((source / 'packinfo.bin').read_bytes()); index[16:32] = bytes(16)
            (source / 'packinfo.bin').write_bytes(index)
            stored = (source / 'd000/pack0000.pak').read_bytes()
            report = extract_pak(source, output, ['/data/example.bin'], native='off')
            record = report['records'][0]
            self.assertEqual(report['stats']['errors'], 1)
            self.assertFalse(record['md5_verified']); self.assertIsNone(record['recovered_name'])
            self.assertEqual((output / record['path']).read_bytes(), stored)
            self.assertTrue(record['path'].endswith('.stored'))

    def test_unsafe_candidate_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'input'
            fixture(source, [('/data/example.bin', b'known', 'verbatim')])
            with self.assertRaises(ValueError):
                extract_pak(source, root / 'out', ['/../escape.bin'], native='off')

    def test_slice_out_of_bounds_remains_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'input'; output = root / 'output'
            fixture(source, [('/data/example.bin', b'known', 'verbatim')])
            index = bytearray((source / 'packinfo.bin').read_bytes())
            struct.pack_into('<I', index, 16 + 24, 1000)
            (source / 'packinfo.bin').write_bytes(index)
            report = extract_pak(source, output, native='off')
            self.assertEqual(report['stats']['errors'], 1)
            self.assertTrue((output / 'raw/d000/pack0000.pak').is_file())

    def test_no_write_into_input_and_no_changed_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'input'; output = root / 'out'
            fixture(source, [('/data/example.bin', b'known', 'verbatim')])
            with self.assertRaises(ValueError):
                extract_pak(source, source / 'output', native='off')
            first = extract_pak(source, output, ['/data/example.bin'], native='off')
            path = output / first['records'][0]['path']; path.write_bytes(b'changed')
            rerun = extract_pak(source, output, ['/data/example.bin'], native='off')
            self.assertEqual(rerun['stats']['errors'], 1)
            self.assertEqual(path.read_bytes(), b'changed')

    def test_unreferenced_pak_is_still_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'input'; output = root / 'out'
            fixture(source, [('/data/example.bin', b'known', 'verbatim')])
            (source / 'orphan.pak').write_bytes(b'unreferenced synthetic bytes')
            manifest = extract_pak(source, output, native='off')
            self.assertEqual((output / 'raw/orphan.pak').read_bytes(), b'unreferenced synthetic bytes')
            self.assertIn('orphan.pak', manifest['raw_sources'])

    def test_cli_reports_failure_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = subprocess.run([sys.executable, '-m', 'legacy', 'pak', '--input',
                                     str(root), '--output', str(root / 'out'), '--native', 'off'],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)


class NamedResourceTests(unittest.TestCase):
    def test_exact_ticks_and_unknown_commands_survive(self):
        chart = chart_events(EVE + b'601,FUTURE_COMMAND,77,extra\nnot a valid event\n')
        self.assertEqual(chart['tick_hz'], 300)
        self.assertEqual(chart['events'][-1], {'tick': 601, 'command': 'FUTURE_COMMAND', 'value': 77,
                                              'extra_columns': ['extra']})
        self.assertEqual(len(chart['unparsed_lines']), 1)

    def test_zero_note_is_not_playable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'source'; source.mkdir()
            for suffix in ['a.eve', 'b.eve', 'e.eve']:
                (source / (SYNTHETIC_ID + suffix)).write_bytes(b'0,MEASURE,0\n300,END,0\n')
            for suffix in ['bgm.bin', 'idx.bin']:
                (source / (SYNTHETIC_ID + suffix)).write_bytes(b'synthetic non-decoded audio')
            manifest = export_layers([('base', source)], root / 'out')
            self.assertEqual(manifest['stats']['source_five_asset_ids'], 1)
            self.assertEqual(manifest['stats']['three_playable_chart_five_asset_ids'], 0)

    def test_layer_selection_is_per_role_and_preserves_originals(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); base = root / 'base'; append = root / 'append'
            base.mkdir(); append.mkdir()
            for suffix in ['a.eve', 'b.eve', 'e.eve']:
                (base / (SYNTHETIC_ID + suffix)).write_bytes(EVE)
            (base / (SYNTHETIC_ID + 'bgm.bin')).write_bytes(b'old bgm')
            (base / (SYNTHETIC_ID + 'idx.bin')).write_bytes(b'original preview')
            (append / (SYNTHETIC_ID + 'bgm.bin')).write_bytes(b'new bgm')
            manifest = export_layers([('base', base), ('append', append)], root / 'out')
            song = manifest['songs'][0]
            self.assertEqual(song['selected_resources']['bgm.bin']['layer'], 'append')
            self.assertEqual(song['selected_resources']['preview.bin']['layer'], 'base')
            self.assertEqual((root / 'out/raw/base' / (SYNTHETIC_ID + 'bgm.bin')).read_bytes(), b'old bgm')
            self.assertEqual(manifest['stats']['source_five_asset_ids'], 1)
            self.assertEqual(manifest['stats']['three_playable_chart_five_asset_ids'], 1)

    def test_named_ifs_members_not_assumed_five_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'input'; source.mkdir()
            (source / (SYNTHETIC_ID + '_msc.ifs')).write_bytes(b'synthetic named archive')
            calls, closed = [], []
            def factory(_):
                members = []
                for name, data in [('folder/bsc.eve', EVE), ('bgm.bin', b'synthetic audio'),
                                   ('idx.bin', b'original short preview'), ('extra/dummy.dat', b'\x00')]:
                    def load(convert_kbin=False, data=data):
                        calls.append(convert_kbin); return data
                    members.append(SimpleNamespace(full_path=name, load=load))
                return SimpleNamespace(manifest=SimpleNamespace(to_text=lambda: '<synthetic/>'),
                                       tree=SimpleNamespace(all_files=members), close=lambda: closed.append(True))
            manifest = export_layers([('base', source)], root / 'out', ifs_factory=factory)
            self.assertEqual(manifest['stats']['ifs_member_files'], 4)
            self.assertEqual(calls, [False] * 4); self.assertEqual(closed, [True])
            self.assertEqual(manifest['stats']['source_five_asset_ids'], 0)
            self.assertEqual(manifest['stats']['source_preview_files'], 1)
            self.assertTrue((root / 'out/unpacked/base' / (SYNTHETIC_ID + '_msc.ifs/extra/dummy.dat')).is_file())

    def test_ifs_traversal_recorded_and_archive_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'input'; source.mkdir()
            (source / 'example.ifs').write_bytes(b'synthetic')
            closed = []
            def factory(_):
                return SimpleNamespace(manifest=SimpleNamespace(to_text=lambda: '<synthetic/>'),
                    tree=SimpleNamespace(all_files=[SimpleNamespace(full_path='../escape', load=lambda **_: b'bad')]),
                    close=lambda: closed.append(True))
            manifest = export_layers([('base', source)], root / 'out', ifs_factory=factory)
            self.assertEqual(manifest['stats']['errors'], 1)
            self.assertEqual(closed, [True])
            self.assertFalse((root / 'escape').exists())
            self.assertTrue((root / 'out/raw/base/example.ifs').is_file())

    def test_same_layer_conflict_is_not_silently_overlaid(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'input'; source.mkdir()
            for folder, payload in [('a', EVE), ('b', EVE.replace(b'PLAY,3', b'PLAY,4'))]:
                (source / folder).mkdir()
                (source / folder / (SYNTHETIC_ID + 'b.eve')).write_bytes(payload)
            manifest = export_layers([('base', source)], root / 'out')
            self.assertEqual(manifest['stats']['errors'], 1)
            self.assertEqual(manifest['stats']['raw_files'], 2)

    def test_optional_conversion_preserves_extras_and_verifies_caches(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'input'; source.mkdir()
            for suffix in ['a.eve', 'b.eve', 'e.eve']:
                (source / (SYNTHETIC_ID + suffix)).write_bytes(EVE)
            for suffix in ['bgm.bin', 'idx.bin']:
                (source / (SYNTHETIC_ID + suffix)).write_bytes(b'BMP\0synthetic audio')
            (source / 'system.bin').write_bytes(b'BMP\0synthetic system audio')
            (source / 'unknown.eve').write_bytes(EVE)
            (source / 'texture.bin').write_bytes(b'PXETsynthetic mocked texture')
            calls = {'audio': 0, 'texture': 0}
            def wav(data, target):
                calls['audio'] += 1
                target.write_bytes(b'RIFF synthetic test output' + data)
                return {'channels': 1, 'sample_rate': 8000, 'frames': 10}
            def texture(_, target, keep_tdxt=False):
                calls['texture'] += 1
                image = target / 'synthetic.png'; image.write_bytes(b'synthetic mocked image output')
                return {'records': [{'status': 'decoded', 'output': image.name,
                                     'sha256': hashlib.sha256(image.read_bytes()).hexdigest()}], 'errors': []}
            modules = {'assets': SimpleNamespace(__path__=[]),
                       'assets.oki4s': SimpleNamespace(bmp_to_wav=wav),
                       'assets.texbin': SimpleNamespace(extract_texbin=texture)}
            with patch.dict(sys.modules, modules):
                manifest = export_layers([('base', source)], root / 'out', decode_audio=True, decode_textures=True)
                self.assertEqual(manifest['stats']['errors'], 0)
                self.assertEqual(manifest['stats']['decoded_wav_files'], 3)
                self.assertEqual(manifest['stats']['extra_event_json_files'], 1)
                repeated = export_layers([('base', source)], root / 'out', decode_audio=True, decode_textures=True)
                self.assertEqual(repeated['stats']['errors'], 0)
                self.assertEqual(calls, {'audio': 3, 'texture': 1})
                (root / 'out/songs' / SYNTHETIC_ID / 'preview.wav').write_bytes(b'changed output')
                failed = export_layers([('base', source)], root / 'out', decode_audio=True, decode_textures=True)
                self.assertEqual(failed['stats']['errors'], 1)
                self.assertEqual((root / 'out/songs' / SYNTHETIC_ID / 'preview.wav').read_bytes(), b'changed output')

    def test_safe_names_and_role_mapping(self):
        for name in ['../x', '/x', 'a/../x', 'C:/x', 'a\\b', 'a//b']:
            with self.assertRaises(ValueError): safe_relative(name)
        self.assertEqual(music_role(SYNTHETIC_ID + 'a.eve'), (SYNTHETIC_ID, 'ADV.eve'))
        self.assertIsNone(music_role('arbitrary.eve'))

    def test_raw_only_ifs_is_not_reported_as_song(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'input'; source.mkdir()
            (source / (SYNTHETIC_ID + '_msc.ifs')).write_bytes(b'\x00')
            manifest = export_layers([('base', source)], root / 'out', extract_ifs=False)
            self.assertEqual(manifest['stats']['song_ids'], 0)
            self.assertEqual(manifest['stats']['source_five_asset_ids'], 0)
            self.assertFalse(manifest['stats']['ifs_extraction_requested'])


if __name__ == '__main__':
    unittest.main()
