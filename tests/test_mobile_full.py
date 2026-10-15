"""Synthetic full-resource backups only; no game content or backup metadata."""
import datetime
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mobile import full_extract as full
from mobile import extract as mobile
import synthetic


@lru_cache(maxsize=3)
def reference_cipher(key):
    """Independent encryption fixture using the project's own public BF tables."""
    source = (ROOT / 'mobile/tools/jbf.c').read_text()
    tables = []
    for name in ('BF_P', 'BF_S0', 'BF_S1', 'BF_S2', 'BF_S3'):
        block = re.search(r'\b' + name + r'\[\d+\]\s*=\s*\{([^}]+)', source).group(1)
        tables.append([int(v, 16) for v in re.findall(r'0x([0-9a-f]+)u', block)])
    p, s = tables[0], tables[1:]
    for i in range(18):
        p[i] ^= int.from_bytes(bytes(key[(4 * i + j) % len(key)] for j in range(4)), 'big')

    def encode(left, right):
        for i in range(16):
            left ^= p[i]
            f = ((s[0][left >> 24] + s[1][(left >> 16) & 255]) & 0xffffffff) ^ ((s[2][(left >> 8) & 255] + s[3][left & 255]) & 0xffffffff)
            right ^= f
            left, right = right, left
        left, right = right, left
        return left ^ p[17], right ^ p[16]

    left = right = 0
    for table in (p, *s):
        for i in range(0, len(table), 2):
            left, right = encode(left, right)
            table[i:i + 2] = [left, right]
    return encode


def encrypted(plain, phrase):
    encode = reference_cipher(hashlib.md5(phrase).digest())
    padded = plain + bytes((-len(plain)) % 8)
    previous, result = bytes.fromhex('e36631da2c85a064'), bytearray()
    for start in range(0, len(padded), 8):
        block = bytes(a ^ b for a, b in zip(padded[start:start + 8], previous))
        previous = struct.pack('>II', *encode(*struct.unpack('>II', block)))
        result.extend(previous)
    return bytes(result) + struct.pack('>II', len(plain), len(result))


def archive_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return buffer.getvalue()


def song(*, empty=False, missing_preview=False, bad_image=False, info3=False):
    meta = {'Name': 'Synthetic Full Song', 'Artist': 'Synthetic Artist', 'LvBas': 1,
            'LvAdv': 2, 'LvExt': 3, 'opaque': b'\x00\xff',
            'date': datetime.datetime(2001, 2, 3), 'uid': plistlib.UID(7)}
    records = [(5, 0, 500000), (3, 0, 0), (2, 600, 0)] if empty else None
    seq = synthetic.sequence(records) + b'opaque seq trailer'
    entries = {'info': plistlib.dumps({'Name': 'Older synthetic title'}),
               'infov2': plistlib.dumps(meta, fmt=plistlib.FMT_BINARY),
               'bgm': b'\x00\x00\x00\x18ftypM4A synthetic full audio',
               'artwork': b'bad image' if bad_image else synthetic.cgbi(),
               'artwork_s': synthetic.cgbi(), 'name_b_e': synthetic.cgbi(),
               **{'seq_' + code: seq for code in ('bas', 'adv', 'ext')}}
    if not missing_preview:
        entries['index'] = b'\x00\x00\x00\x18ftypM4A synthetic original preview'
    encrypted_entries = {name: synthetic.encrypted(data) for name, data in entries.items()}
    encrypted_entries['opaque_unknown'] = b'opaque not encrypted'
    if info3:
        encrypted_entries['infov3'] = encrypted(b'HEAD' + plistlib.dumps(meta, fmt=plistlib.FMT_BINARY), full.ALTERNATE_PHRASES['infov3'])
    return archive_bytes(encrypted_entries)


def backup(path, songs=None, extras=True, bad_frame=False):
    jbt = songs or {'000000001': song()}
    with zipfile.ZipFile(path, 'w') as z:
        for sid, blob in jbt.items():
            z.writestr('example.app/Documents/' + sid + '.jbt', blob)
        if extras:
            texture = lambda data: encrypted(b'HEAD' + data, full.ALTERNATE_PHRASES['texture'])
            frames = {'ma000': texture(synthetic.cgbi()), 'h1000': texture(synthetic.cgbi()),
                      'opaque_extra': b'unrecognized game bytes', '__MACOSX/._ma000': b'residue'}
            if bad_frame:
                frames['h2000'] = texture(b'not an image')
            z.writestr('example.app/Library/Private Documents/marker/synthetic.zip', archive_bytes(frames))
            z.writestr('example.app/Library/Private Documents/appendData/synthetic.zip', archive_bytes({
                'share.png': texture(synthetic.cgbi()), 'filename.txt': b'share.png\n'}))
            z.writestr('example.app/Documents/scImg/synthetic.img', synthetic.cgbi())
            z.writestr('icon.png', synthetic.cgbi())
        # These must never appear in any exported file or source inventory.
        for name in ('Library/Preferences/settings.plist', 'Documents/score.sqlite',
                     'Documents/accounts.dat', 'Documents/scImgExtra/camera.jpg'):
            z.writestr('example.app/' + name, b'PRIVATE SENTINEL DO NOT EXPORT')
        z.writestr('Manifest.plist', plistlib.dumps({'DeviceSerial': 'PRIVATE SENTINEL DO NOT EXPORT'}))
    return jbt


PROBE = {'streams': [{'codec_type': 'audio', 'codec_name': 'aac', 'channels': 2, 'sample_rate': '44100'}]}


class FullMobileTests(unittest.TestCase):
    def extract(self, root, **kwargs):
        options = dict(jobs=2, ffprobe='synthetic-ffprobe', mc=True)
        options.update(kwargs)
        with patch.object(full, 'probe', return_value=PROBE):
            return full.Extractor(root / 'source.adbk', root / 'out', 'jubeatplus', **options).run()

    def test_full_preservation_and_extras(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            originals = backup(root / 'source.adbk', {'000000001': song(info3=True)})
            stats = self.extract(root)
            out = root / 'out'
            row = json.loads((out / 'manifest.json').read_text())[0]
            self.assertEqual(stats['errors'], 0)
            self.assertEqual(stats['status'], 'complete')
            self.assertEqual((out / 'raw/000000001.jbt').read_bytes(), originals['000000001'])
            with zipfile.ZipFile(io.BytesIO(originals['000000001'])) as z:
                for name in ('bgm', 'index'):
                    role = full.AUDIO[name]
                    self.assertEqual((out / row['audio'][role]['path']).read_bytes(), mobile.Decryptor()(z.read(name)))
            self.assertNotEqual(row['audio']['bgm']['sha256'], row['audio']['preview']['sha256'])
            self.assertEqual(row['audio']['preview']['source_member'], 'index')
            self.assertEqual(row['metadata']['infov2']['opaque']['$base64'], 'AP8=')
            self.assertEqual(row['metadata']['infov2']['uid']['$value'], 7)
            self.assertEqual(row['metadata']['infov2']['date']['$iso8601'], '2001-02-03T00:00:00')
            self.assertEqual(set(row['metadata']), {'info', 'infov2', 'infov3'})
            self.assertEqual(row['unknown_members'], ['opaque_unknown'])
            self.assertEqual(row['asset_completeness']['complete_five_assets'], True)
            self.assertEqual(stats['extras_images_decoded'], 5)
            self.assertEqual(stats['marker_frame_images'], 2)
            self.assertEqual(stats['theme_images_decoded'], 1)
            self.assertEqual(stats['scimg_exported'], 1)
            self.assertEqual(stats['unknown_extra_members_preserved'], 1)
            extras = json.loads((out / 'extras/manifest.json').read_text())
            self.assertTrue(any(i['plaintext_prefix_bytes'] == 4 for e in extras for i in e['images']))
            self.assertTrue(any(m.get('status') == 'plain_text_preserved' for e in extras for m in e['members']))
            for p in out.rglob('*'):
                if p.is_file():
                    self.assertNotIn(b'PRIVATE SENTINEL', p.read_bytes())
            self.assertIsNone(json.loads((out / 'app_version.json').read_text())['app_version'])

    def test_complete_raw_empty_charts_are_not_playable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backup(root / 'source.adbk', {'000000001': song(empty=True)}, extras=False)
            stats = self.extract(root, mc=False)
            self.assertEqual(stats['errors'], 0)
            self.assertEqual(stats['songs_complete'], 1)
            self.assertEqual(stats['raw_five_assets_preserved'], 1)
            self.assertEqual(stats['complete_five_assets'], 0)
            self.assertEqual(stats['three_playable_charts'], 0)
            self.assertEqual(stats['zero_note_charts'], 3)

    def test_full_tick_unknown_header_and_tail(self):
        raw = bytearray(synthetic.sequence([(5, 0, 500000), (99, 301, 0x12345678), (6, 302, (1 << 8) | (1 << 6) | (1 << 4))]))
        raw[0x28:0x2c] = b'HEAD'
        data = bytes(raw) + b'TAIL'
        parsed = full.seq_events(data)
        self.assertEqual(parsed['header_hex'], bytes(raw[:0x60]).hex())
        self.assertEqual(parsed['trailing_bytes_hex'], b'TAIL'.hex())
        self.assertEqual(parsed['unknown_types'], [99])
        self.assertEqual(parsed['events'][1]['raw_r0'], 99 | (301 << 8))
        self.assertEqual(parsed['events'][1]['command'], 'TYPE_99')
        self.assertEqual(parsed['events'][2]['long']['duration_tick'], 1)
        self.assertEqual(parsed['events'][2]['long']['end_tick'], 303)
        for bad in (b'bad', data[:0x61], b'FAKE' + data[4:]):
            with self.assertRaises(ValueError):
                full.seq_events(bad)

    def test_failure_records_and_originals_stay(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backup(root / 'source.adbk', {'000000001': song(missing_preview=True, bad_image=True)}, bad_frame=True)
            stats = self.extract(root)
            self.assertEqual(stats['status'], 'partial')
            self.assertEqual(stats['preview_missing'], 1)
            self.assertEqual(stats['preview_preserved'], 0)
            self.assertEqual(stats['image_failures'], 2)
            self.assertTrue((root / 'out/songs/000000001/decrypted/artwork').exists())
            self.assertTrue((root / 'out/extras/unpacked/marker/synthetic.zip/decrypted/h2000').exists())
            self.assertGreater(stats['errors'], 0)

    def test_probe_failure_retains_both_audio_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backup(root / 'source.adbk', extras=False)
            with patch.object(full, 'probe', side_effect=ValueError('synthetic probe failure')):
                stats = full.Extractor(root / 'source.adbk', root / 'out', 'jukebeat').run()
            self.assertEqual((stats['bgm_preserved'], stats['preview_preserved']), (1, 1))
            self.assertEqual((stats['bgm_failed'], stats['preview_failed']), (1, 1))
            self.assertEqual(stats['raw_five_assets_preserved'], 1)
            self.assertEqual(stats['complete_five_assets'], 0)
            row = json.loads((root / 'out/manifest.json').read_text())[0]
            for audio in row['audio'].values():
                self.assertTrue((root / 'out' / audio['path']).exists())

    def test_key_isolation_and_concurrent_constructors(self):
        song_decode = mobile.Decryptor()
        phrases = full.ALTERNATE_PHRASES
        plain = b'HEAD' + synthetic.cgbi()
        blobs = {kind: encrypted(plain, phrase) for kind, phrase in phrases.items()}
        song_blob = synthetic.encrypted(b'song-key payload')
        def worker(index):
            kind = ('texture', 'infov3')[index % 2]
            return full.AlternateDecoder(kind)(blobs[kind]), song_decode(song_blob)
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(worker, range(64))), [(plain, b'song-key payload')] * 64)
        with self.assertRaises(ValueError):
            full.AlternateDecoder('texture')(b'bad')

    def test_unsafe_names_duplicates_and_fresh_output(self):
        for name in ('../escape', '/absolute', 'C:/drive', 'a\\b', 'a/./b', 'a//b'):
            with self.assertRaises(ValueError):
                full.safe_member(name)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            unsafe = archive_bytes({'../escape': b'raw', 'seq_bas': synthetic.encrypted(synthetic.sequence())})
            backup(root / 'source.adbk', {'000000001': unsafe}, extras=False)
            stats = self.extract(root)
            self.assertEqual(stats['status'], 'partial')
            self.assertTrue((root / 'out/raw/000000001.jbt').exists())
            self.assertFalse((root / 'escape').exists())
            with self.assertRaisesRegex(ValueError, 'new or empty'):
                self.extract(root)
        with zipfile.ZipFile(io.BytesIO(archive_bytes({'Name': b'a', 'name': b'b'}))) as z:
            with self.assertRaisesRegex(ValueError, 'case-colliding'):
                full.checked_infos(z)

    def test_application_version_only_from_matching_app_info(self):
        entries = {
            'example.app/Info.plist': plistlib.dumps({
                'CFBundleIdentifier': 'jp.konami.jubeatplus',
                'CFBundleShortVersionString': '0.0.synthetic', 'CFBundleVersion': '42',
                'DeviceSerial': 'PRIVATE SENTINEL DO NOT EXPORT'}),
            'Info.plist': plistlib.dumps({'DeviceSerial': 'PRIVATE SENTINEL DO NOT EXPORT'}),
            'example.app/Library/Preferences/settings.plist': plistlib.dumps({'internal_version': 'not evidence'}),
        }
        with zipfile.ZipFile(io.BytesIO(archive_bytes(entries))) as z:
            info = full.application_metadata(z, 'jubeatplus')
            self.assertEqual(info['app_version'], '0.0.synthetic')
            self.assertEqual(info['app_build'], '42')
            self.assertNotIn('PRIVATE SENTINEL', json.dumps(info))
            self.assertIsNone(full.application_metadata(z, 'jukebeat')['app_version'])
        entries['other.app/Info.plist'] = plistlib.dumps({
            'CFBundleIdentifier': 'jp.konami.jubeatplus', 'CFBundleShortVersionString': 'conflict'})
        with zipfile.ZipFile(io.BytesIO(archive_bytes(entries))) as z:
            self.assertIsNone(full.application_metadata(z, 'jubeatplus')['app_version'])

    def test_non_numeric_package_is_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backup(root / 'source.adbk', {'unsupported-id': song()}, extras=False)
            with self.assertRaisesRegex(ValueError, 'nonnumeric'):
                self.extract(root)

    def test_limited_run_not_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backup(root / 'source.adbk', {'000000001': song(), '000000002': song()}, extras=False)
            stats = self.extract(root, limit=1)
            self.assertEqual(stats['processed_song_packages'], 1)
            self.assertEqual(stats['expected_song_packages'], 2)
            self.assertTrue(stats['limited_sample'])
            self.assertEqual(stats['status'], 'partial')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'optional FFmpeg integration')
    def test_synthetic_aac_probe_integration(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'synthetic.m4a'
            buffer = io.BytesIO()
            with wave.open(buffer, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(8000)
                wav.writeframes(bytes(1600))
            subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-f', 'wav', '-i', 'pipe:0',
                            '-c:a', 'aac', str(path)], input=buffer.getvalue(), check=True)
            info = full.probe(path, shutil.which('ffprobe'))
            self.assertEqual(info['streams'][0]['codec_name'], 'aac')
            with self.assertRaises(ValueError):
                broken = Path(temp) / 'broken.m4a'
                broken.write_bytes(b'invalid synthetic audio')
                full.probe(broken, shutil.which('ffprobe'))


if __name__ == '__main__':
    unittest.main()
