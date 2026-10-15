import ctypes
import json
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mobile import extract as mobile
from mobile.tools.cgbi import cgbi_to_png
from arcade import import_ifs as arcade
import synthetic


class CodecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.decode = mobile.Decryptor()

    def test_reference_cipher_and_concurrency(self):
        inputs = [b'hello', b'12345678', b'synthetic test' * 31, b'']
        ciphertext = [synthetic.encrypted(p) for p in inputs]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.decode, ciphertext * 32))
        self.assertEqual(results, inputs * 32)
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(lambda _: mobile.Decryptor()(ciphertext[0]), range(8))), [inputs[0]] * 8)

    def test_reject_short_buffer_without_writing(self):
        blob = synthetic.encrypted(b'x')
        dest = ctypes.create_string_buffer(b'ZZZZZZZZ', 8)
        size = self.decode._lib.jbf_decrypt(self.decode._key, 16, blob, len(blob), dest, 1)
        self.assertLess(size, 0)
        self.assertEqual(dest.raw, b'ZZZZZZZZ')

    def test_bad_cipher_trailer(self):
        for blob in [b'x', synthetic.encrypted(b'test')[:-1], bytes(9)]:
            with self.assertRaises(mobile.DecryptError): self.decode(blob)

    def test_cgbi_pixels(self):
        data = cgbi_to_png(synthetic.cgbi()); self.assertNotIn(b'CgBI', data)
        start = data.index(b'IDAT'); length = struct.unpack_from('>I', data, start - 4)[0]
        self.assertEqual(zlib.decompress(data[start + 4:start + 4 + length]), bytes([0, 199, 100, 50, 128]))
        self.assertEqual(cgbi_to_png(data), data)

    def test_cgbi_budgets(self):
        for bad in [synthetic.cgbi()[:-5], synthetic.cgbi(raw=bytes(200)), synthetic.cgbi(width=100000, height=100000)]:
            with self.assertRaises(ValueError): cgbi_to_png(bad)

    def test_song_allowlist(self):
        song = mobile.process_song(synthetic.song_zip({'../unexpected': b'private synthetic data'}), '00000001', self.decode)
        self.assertEqual(set(song['charts']), {'bas', 'adv', 'ext'})
        self.assertNotIn('extra', song); self.assertEqual(song['audio'], b'synthetic audio bytes')


class ChartTests(unittest.TestCase):
    def test_mobile_timing(self):
        c = mobile.parse_chart(synthetic.sequence())
        self.assertEqual(c['notes'], [(Fraction(0), 0, None, None), (Fraction(2), 0, Fraction(4), 4), (Fraction(5), 2, None, None)])
        self.assertEqual(mobile.volume(c['notes']), (4, 1))

    def test_bad_tempo_and_panel(self):
        for records in [[(5, 0, 0)], [(5, 1, 500000)], [(5, 0, 500000), (1, 0, 99)]]:
            with self.assertRaises(mobile.ChartError): mobile.parse_chart(synthetic.sequence(records))
        for value in [0, -1]:
            with self.assertRaises(arcade.ChartError): arcade._value_to_bpm(value)

    def test_small_positive_bpm(self): self.assertGreater(arcade._value_to_bpm(100000000), 0)

    def test_ifs_and_mobile_agree(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'sample.ifs'; p.write_bytes(synthetic.ifs_bytes())
            notes, timing, offset = arcade.parse_chart(p, 'EXT')
        c = mobile.parse_chart(synthetic.sequence())
        self.assertEqual(notes, c['notes']); self.assertEqual(timing, c['timing']); self.assertEqual(offset, 0)

    def test_short_hold_preserved(self):
        hold = (1 << 8) | (1 << 6) | (1 << 4)
        c = mobile.parse_chart(synthetic.sequence([(5, 0, 500000), (6, 0, hold)]))
        self.assertEqual(c['notes'][0][0], c['notes'][0][2]); self.assertEqual(mobile.volume(c['notes']), (2, 1))

    def test_ifs_bounds(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'bad.ifs'
            for data in [b'bad', synthetic.ifs_bytes()[:-10]]:
                p.write_bytes(data)
                with self.assertRaises(arcade.ChartError): arcade.load_ifs(p)

    def test_audio_decoder(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'sample.ifs'; p.write_bytes(synthetic.ifs_bytes())
            pcm, rate, channels = arcade.decode_bmp_to_pcm(arcade.read_block(p, 0))
        self.assertEqual((len(pcm), rate, channels), (64, 8000, 2)); self.assertNotEqual(pcm, bytes(len(pcm)))


class OutputTests(unittest.TestCase):
    def test_arcade_original_preview_block(self):
        # A distinct, artificial preview proves that block 1 is decoded,
        # rather than taking a cut of block 0 or reusing its output.
        preview = bytearray(32)
        preview[:4] = b'BMP\0'
        struct.pack_into('>I', preview, 4, 4)
        preview[16] = 1
        struct.pack_into('>I', preview, 20, 4000)
        preview = bytes(preview) + b'\x10\x10'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); src = root / 'song.ifs'; out = root / 'song.mcz'
            src.write_bytes(synthetic.ifs_bytes(preview=preview))
            with patch.object(arcade, 'encode_vorbis', side_effect=[b'BGM OggS', b'PREVIEW OggS']) as encode:
                stats = arcade.build_mcz(ifs=src, cover=b'', mid='00000001', title='Example',
                    artist='Example', levels={'BSC': 1, 'ADV': 2, 'EXT': 3}, out_path=out,
                    with_preview=True)
            with zipfile.ZipFile(out) as z:
                self.assertEqual(z.read('0/bgm.ogg'), b'BGM OggS')
                self.assertEqual(z.read('0/preview.ogg'), b'PREVIEW OggS')
                chart = json.loads(z.read('0/Example_BSC Lv1.mc'))
                self.assertEqual(chart['note'][0]['sound'], 'bgm.ogg')
            import wave, io
            with wave.open(io.BytesIO(encode.call_args_list[1].args[0])) as wav:
                self.assertEqual((wav.getnchannels(), wav.getframerate(), wav.getnframes()), (1, 4000, 4))
                self.assertEqual(wav.readframes(4), struct.pack('<4h', 96, 128, 224, 256))
            self.assertEqual(stats['preview_status'], 'exported')
            self.assertEqual(stats['preview_bytes'], len(b'PREVIEW OggS'))

    def test_arcade_preview_empty_disabled_or_invalid(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); src = root / 'song.ifs'
            kwargs = dict(ifs=src, cover=b'', mid='00000001', title='Example', artist='',
                          levels={'BSC': 1, 'ADV': 2, 'EXT': 3})
            for index, (raw, enabled, status) in enumerate([(b'', True, 'absent'), (b'bad audio', False, 'not_requested')]):
                src.write_bytes(synthetic.ifs_bytes(preview=raw)); out = root / f'{index}.mcz'
                with patch.object(arcade, 'bmp_to_ogg', return_value=b'OggS') as decode:
                    stats = arcade.build_mcz(**kwargs, out_path=out, with_preview=enabled)
                self.assertEqual(decode.call_count, 1)
                self.assertEqual(stats['preview_status'], status)
                with zipfile.ZipFile(out) as z: self.assertNotIn('0/preview.ogg', z.namelist())
            src.write_bytes(synthetic.ifs_bytes(preview=b'bad audio'))
            with patch.object(arcade, 'encode_vorbis', return_value=b'OggS'):
                with self.assertRaises(arcade.AudioError):
                    arcade.build_mcz(**kwargs, out_path=root / 'invalid.mcz', with_preview=True)
            self.assertFalse((root / 'invalid.mcz').exists())
            with self.assertRaises(ValueError):
                arcade.build_mcz(**kwargs, out_path=root / 'conflict.mcz', charts_only=True, with_preview=True)

    def test_transcode_fallback_and_input_errors(self):
        failed = subprocess.CompletedProcess([], 8, b'', b"Unknown encoder 'libvorbis'")
        success = subprocess.CompletedProcess([], 0, b'OggS synthetic', b'')
        with patch.object(mobile.shutil, 'which', return_value='ffmpeg'), patch.object(mobile.subprocess, 'run', side_effect=[failed, success]) as run:
            self.assertEqual(mobile.to_ogg(b'input'), b'OggS synthetic')
            self.assertIn('vorbis', run.call_args_list[1].args[0])
        failed.stderr = b'Invalid input'
        with patch.object(mobile.shutil, 'which', return_value='ffmpeg'), patch.object(mobile.subprocess, 'run', return_value=failed) as run:
            with self.assertRaises(RuntimeError):
                mobile.to_ogg(b'input')
            self.assertEqual(run.call_count, 1)

    def test_mcz_transcoded_references(self):
        chart = mobile.parse_chart(synthetic.sequence())
        for audio, cover in [(b'synthetic audio', b'png'), (b'', b'')]:
            with tempfile.TemporaryDirectory() as temp, patch.object(mobile, 'to_ogg', return_value=b'OggS synthetic'):
                p = Path(temp) / 'out.mcz'
                mobile.write_mcz(p, 'Example', 'Example', {'bas': 1}, {'bas': chart}, cover, audio, True, 'jkt.png')
                with zipfile.ZipFile(p) as z:
                    mc = json.loads(z.read('0/Example_BSC Lv1.mc'))
                    for note in mc['note']:
                        if 'sound' in note: self.assertIn('0/' + note['sound'], z.namelist())
                    if mc['meta']['cover']: self.assertIn('0/' + mc['meta']['cover'], z.namelist())
                    if audio: self.assertEqual(mc['note'][0]['sound'], 'bgm.ogg')

    def test_loose_chart_references(self):
        song = mobile.process_song(synthetic.song_zip(), '00000001', mobile.Decryptor())
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'song'; mobile.dump_song(song, p, False, False)
            mc = json.loads((p / 'BSC Lv1.mc').read_text())
            self.assertTrue((p / mc['meta']['cover']).exists()); self.assertTrue((p / mc['note'][0]['sound']).exists())

    def test_zip_publication_failure_and_existing_target(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'out.mcz'
            with self.assertRaises(ValueError):
                with arcade.atomic_zip(p) as z:
                    z.writestr('a', 'a'); raise ValueError('synthetic failure')
            self.assertFalse(p.exists()); self.assertEqual(list(Path(temp).iterdir()), [])
            p.write_bytes(b'keep')
            with self.assertRaises(FileExistsError):
                with arcade.atomic_zip(p) as z: z.writestr('a', 'new')
            self.assertEqual(p.read_bytes(), b'keep')

    def test_bad_metadata(self):
        example = json.loads((ROOT / 'examples/arcade-songs.example.json').read_text())[0]
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'songs.json'
            for field, value in [('version', '../escape'), ('mid', '../bad'), ('levels', {}), ('title', None)]:
                p.write_text(json.dumps([{**example, field: value}]))
                with self.assertRaises(ValueError): arcade.load_song_list(p)

    def test_mobile_cli(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); package = root / 'jubeatplus.adbk'; synthetic.backup(package)
            out = root / 'out'
            command = [sys.executable, str(ROOT / 'mobile/extract.py'), '--packages', str(package), '--pack', 'jubeatplus', '--out', str(out), '--mcz', '--jobs', '2']
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((out / 'report.json').read_text())['counts'], {'jubeatplus': 1})
            self.assertEqual(len(list(out.rglob('*.mcz'))), 1); self.assertFalse(list(out.rglob('*private-settings*')))
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)

    def test_mobile_cli_failure_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); package = root / 'jubeatplus.adbk'; synthetic.backup(package, bad=True)
            result = subprocess.run([sys.executable, str(ROOT / 'mobile/extract.py'), '--packages', str(package), '--pack', 'jubeatplus', '--out', str(root / 'out')], capture_output=True)
            self.assertNotEqual(result.returncode, 0); self.assertFalse((root / 'out/report.json').exists())

    def test_arcade_cli(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); path = root / 'data/ifs_pack/d0000000/00000001_msc.ifs'
            path.parent.mkdir(parents=True); path.write_bytes(synthetic.ifs_bytes())
            result = subprocess.run([sys.executable, str(ROOT / 'arcade/import_ifs.py'), '--songs', str(ROOT / 'examples/arcade-songs.example.json'), '--extract', str(root), '--out', str(root / 'out'), '--charts-only'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            files = list((root / 'out').rglob('*.mcz')); self.assertEqual(len(files), 1)
            with zipfile.ZipFile(files[0]) as z:
                self.assertEqual(len(z.namelist()), 3)
                for name in z.namelist():
                    chart = json.loads(z.read(name))
                    self.assertEqual(chart['meta']['cover'], ''); self.assertFalse(any('sound' in n for n in chart['note']))

if __name__ == '__main__': unittest.main()
