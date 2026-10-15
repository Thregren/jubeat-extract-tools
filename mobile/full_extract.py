#!/usr/bin/env python3
"""Full-fidelity jubeat plus / jukebeat resource export from ZIP-based backups.

Original JBTs and plaintext members are preserved; previews come from index.
Only song packages and installed game assets are selected from the backup.
See docs/mobile-full-assets.md for scope, dependencies and format references.
"""
from __future__ import annotations

import argparse
import base64
import collections
import ctypes
import datetime
from xml.parsers.expat import ExpatError
import hashlib
import io
import json
import math
import os
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

if __package__:
    from . import extract as mobile
else:
    import extract as mobile

SCHEMA = 1
COMMANDS = {1: 'PLAY', 2: 'END', 3: 'MEASURE', 4: 'HAKU', 5: 'TEMPO', 6: 'LONG'}
CODES = {'bas': 'BSC', 'bsc': 'BSC', 'adv': 'ADV', 'ext': 'EXT'}
DIFFICULTIES = ('BSC', 'ADV', 'EXT')
IMAGES = {'artwork', 'artwork_s', 'name_b', 'name_w', 'name_b_e', 'name_w_e'}
AUDIO = {'bgm': 'bgm', 'index': 'preview'}
PLIST_MAGIC = (b'bplist00', b'<?xml')
SEQ_MAGIC = (b'IJBQ', b'IJSQ', b'IJBS', b'JBSQ')
MAX_MEMBER = 64 * 1024 * 1024
MAX_JBT = 128 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
# Format facts: Tatsh/dade, MIT; see the exact source links in the documentation.
ALTERNATE_PHRASES = {
    'texture': b'copious plus knit ripples',
    'infov3': b'Konami Bemani Mobile iOS',
}
_ALT_LIBS = {}
_ALT_LOCK = threading.Lock()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def jsonable(value):
    """Lossless plist types in JSON; original plist remains authoritative."""
    if isinstance(value, bytes):
        return {'$type': 'bytes', '$base64': base64.b64encode(value).decode('ascii')}
    if isinstance(value, datetime.datetime):
        return {'$type': 'datetime', '$iso8601': value.isoformat()}
    if isinstance(value, plistlib.UID):
        return {'$type': 'plist_uid', '$value': value.data}
    if isinstance(value, dict):
        return {str(key): jsonable(v) for key, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return {'$type': 'float', '$value': str(value)}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError('Unsupported plist value: ' + type(value).__name__)


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_bytes(data)
    temporary.replace(path)


def js(path, value):
    dump(path, (json.dumps(jsonable(value), ensure_ascii=False,
                           indent=2, allow_nan=False) + '\n').encode('utf-8'))


def rel(path, out):
    return path.relative_to(out).as_posix()


def safe_member(name):
    """ZIP names are POSIX paths. Reject aliases and platform-specific escapes."""
    p = PurePosixPath(name)
    if (not name or '\\' in name or '\x00' in name or ':' in name
            or p.is_absolute() or any(x in ('', '.', '..') for x in name.split('/'))):
        raise ValueError('Unsafe or ambiguous archive member name')
    return Path(*p.parts)


def checked_infos(z, maximum=1000):
    infos = z.infolist()
    if len(infos) > maximum or sum(x.file_size for x in infos) > MAX_EXPANDED:
        raise ValueError('Archive entry/expanded-size budget exceeded')
    seen = set()
    for item in infos:
        if item.is_dir():
            continue
        path = safe_member(item.filename)
        # Casefold also rejects collisions on common macOS/Windows filesystems.
        key = path.as_posix().casefold()
        if key in seen:
            raise ValueError('Duplicate or case-colliding archive member name')
        seen.add(key)
        if (item.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError('ZIP symbolic links are unsupported')
    return infos


def read_member(z, item, maximum=MAX_MEMBER):
    if item.file_size > maximum:
        raise ValueError('Member exceeds configured read budget')
    data = z.read(item)  # zipfile verifies the uncompressed CRC.
    if len(data) != item.file_size:
        raise ValueError('ZIP member size mismatch')
    return data


class AlternateDecoder:
    """One immutable key schedule per shared-library image, shared by workers."""
    def __init__(self, kind):
        self.key = hashlib.md5(ALTERNATE_PHRASES[kind]).digest()
        with _ALT_LOCK:
            if kind not in _ALT_LIBS:
                if sys.platform not in ('darwin', 'linux'):
                    raise RuntimeError('Automatic native build supports macOS/Linux; use WSL on Windows')
                compiler = shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
                if not compiler:
                    raise RuntimeError('A C compiler is required')
                build = Path(__file__).resolve().parents[1] / '.build'
                build.mkdir(exist_ok=True)
                suffix = '.dylib' if sys.platform == 'darwin' else '.so'
                target = build / ('libmobile_' + kind + suffix)
                source = Path(__file__).resolve().parent / 'tools' / 'marker.c'
                codec = source.with_name('jbf.c')
                newest = max(source.stat().st_mtime, codec.stat().st_mtime)
                if not target.exists() or target.stat().st_mtime < newest:
                    temporary = target.with_name(target.name + '.' + str(os.getpid()) + '.tmp')
                    flags = ['-dynamiclib'] if sys.platform == 'darwin' else ['-shared', '-fPIC']
                    subprocess.run([compiler, '-O2', *flags, str(source), '-o', str(temporary)],
                                   check=True, timeout=120)
                    temporary.replace(target)
                lib = ctypes.CDLL(str(target))
                lib.marker_setkey.argtypes = [ctypes.c_char_p, ctypes.c_int]
                lib.marker_setkey.restype = ctypes.c_long
                lib.marker_decrypt.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                                              ctypes.c_long, ctypes.c_char_p, ctypes.c_long]
                lib.marker_decrypt.restype = ctypes.c_long
                if lib.marker_setkey(self.key, len(self.key)):
                    raise ValueError('Alternate BFCodec key setup failed')
                _ALT_LIBS[kind] = lib
            self.lib = _ALT_LIBS[kind]

    def __call__(self, blob):
        if not framed(blob) or len(blob) > MAX_MEMBER:
            raise ValueError('Invalid alternate BFCodec envelope')
        output = ctypes.create_string_buffer(len(blob) + 8)
        n = self.lib.marker_decrypt(self.key, len(self.key), blob, len(blob), output, len(output))
        if n < 0:
            raise ValueError('Alternate BFCodec decryption failed')
        return output.raw[:n]


def framed(blob):
    if len(blob) < 8:
        return False
    plain, cipher = struct.unpack_from('>II', blob, len(blob) - 8)
    return cipher == len(blob) - 8 and cipher % 8 == 0 and 0 <= plain <= cipher and cipher - plain < 8


def image_kind(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if data.startswith(b'\xff\xd8'):
        return 'jpg'
    if data.startswith(b'GIF8'):
        return 'gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    return None


def has_image(data):
    return image_kind(data) or image_kind(data[4:])


def audio_ext(data):
    if data[4:8] == b'ftyp':
        return '.m4a'
    if len(data) > 1 and data[0] == 255 and data[1] & 0xf6 == 0xf0:
        return '.aac'
    if data[:4] == b'RIFF':
        return '.wav'
    if data[:3] == b'ID3':
        return '.mp3'
    return '.bin'


def decrypt_member(blob, name, decode):
    if name == 'infov3' and blob[4:].startswith(PLIST_MAGIC):
        return blob, 'already_plain'
    if name.startswith('seq_') and blob[:4] in SEQ_MAGIC:
        return blob, 'already_plain'
    if (has_image(blob) or blob.startswith(PLIST_MAGIC)
            or audio_ext(blob) != '.bin'):
        return blob, 'already_plain'
    if framed(blob):
        return decode(blob), 'bfcodec_decrypted'
    if name in IMAGES | set(AUDIO) | {'info', 'infov2', 'infov3'} or name.startswith('seq_'):
        raise ValueError('Known resource has invalid BFCodec length footer/signature')
    return None, 'unknown_original_preserved'


def export_image(data, path, out):
    from PIL import Image
    prefix = 0
    if not image_kind(data) and image_kind(data[4:]):
        prefix, data = 4, data[4:]
    kind = image_kind(data)
    if not kind:
        raise ValueError('Unsupported image signature')
    cgbi = kind == 'png' and b'CgBI' in data[:64]
    converted = mobile.cgbi_to_png(data) if kind == 'png' else data
    with Image.open(io.BytesIO(converted)) as image:
        if image.width * image.height > 16_000_000:
            raise ValueError('Image pixel budget exceeded')
        # Verify every frame, retaining the original animation container if present.
        frames = getattr(image, 'n_frames', 1)
        for index in range(frames):
            image.seek(index)
            image.load()
        width, height, mode = image.width, image.height, image.mode
    target = path.with_name(path.name + '.' + kind)
    dump(target, converted)
    return {'path': rel(target, out), 'sha256': sha(converted), 'width': width,
            'height': height, 'mode': mode, 'format': kind, 'frames': frames,
            'cgbi_converted': cgbi, 'plaintext_prefix_bytes': prefix, 'status': 'decoded'}


def probe(path, executable):
    proc = subprocess.run([executable, '-v', 'error', '-show_entries',
                           'stream=codec_type,codec_name,sample_rate,channels,duration',
                           '-show_entries', 'format=format_name,duration,size',
                           '-of', 'json', str(path)], capture_output=True, timeout=90)
    if proc.returncode:
        raise ValueError(proc.stderr.decode('utf-8', errors='replace')[:4000])
    info = json.loads(proc.stdout)
    if not any(s.get('codec_type') == 'audio' for s in info.get('streams', [])):
        raise ValueError('No audio stream found')
    return info


def seq_events(data):
    """Preserve every 300 Hz record, not the optional MC quantization."""
    magic, count, notes, total, mask, records = mobile.parse_seq(data)
    if magic not in SEQ_MAGIC:
        raise ValueError('Unrecognized seq magic; plaintext retained')
    events, commands = [], collections.Counter()
    for index, (kind, tick, value) in enumerate(records):
        command = COMMANDS.get(kind, 'TYPE_' + str(kind))
        commands[command] += 1
        raw0, raw1 = struct.unpack_from('<II', data, 0x60 + index * 8)
        event = {'record_index': index, 'byte_offset': 0x60 + index * 8, 'raw_r0': raw0,
                 'raw_r1': raw1, 'type': kind, 'tick': tick, 'command': command, 'value': value}
        if kind == 6:
            panel, direction, length, duration = value & 15, (value >> 4) & 3, (value >> 6) & 3, value >> 8
            dx, dy = mobile._DIR_VEC[direction]
            event['long'] = {'index': panel, 'direction': direction, 'length': length,
                             'duration_tick': duration, 'end_tick': tick + duration,
                             'end_index': panel % 4 + dx * length + 4 * (panel // 4 + dy * length)}
        events.append(event)
    return {'tick_hz': 300, 'magic_ascii': magic.decode('ascii'), 'magic_hex': magic.hex(),
            'record_count': count, 'header_note_count': notes, 'total_tick': total,
            'marker_mask': mask, 'header_hex': data[:0x60].hex(),
            'trailing_bytes_hex': data[0x60 + count * 8:].hex(), 'events': events,
            'commands': dict(commands), 'unknown_types': sorted(set(e['type'] for e in events) - set(COMMANDS))}


def classify(row):
    raw = [code for code in DIFFICULTIES if code in row['charts']]
    playable = [code for code in raw if row['charts'][code]['playable']]
    audio_present = all(role in row['audio'] for role in ('bgm', 'preview'))
    validated = all(row['audio_status'][role] == 'validated' for role in ('bgm', 'preview'))
    return {'raw_chart_codes': raw, 'playable_chart_codes': playable,
            'three_raw_charts_present': len(raw) == 3, 'three_playable_charts': len(playable) == 3,
            'bgm_original_validated': row['audio_status']['bgm'] == 'validated',
            'preview_original_validated': row['audio_status']['preview'] == 'validated',
            'raw_five_assets_preserved': len(raw) == 3 and audio_present,
            'complete_five_assets': len(playable) == 3 and validated}


def select_song(item):
    parts = PurePosixPath(item.filename).parts
    return (not item.is_dir() and 'Documents' in parts and item.filename.lower().endswith('.jbt'))


def extra_destination(name):
    """Return only explicitly selected game-asset paths; never read private state."""
    parts = PurePosixPath(name).parts
    if name == 'icon.png':
        return Path('app_icon.png'), 'icon'
    for index, part in enumerate(parts):
        if part == 'Documents' and parts[index + 1:index + 2] == ('scImg',):
            return Path('scImg') / safe_member('/'.join(parts[index + 2:])), 'scimg'
        if part == 'Library' and parts[index + 1:index + 2] == ('Private Documents',):
            tail = parts[index + 2:]
            if len(tail) > 1 and tail[0] in ('marker', 'appendData'):
                return safe_member('/'.join(tail)), 'marker' if tail[0] == 'marker' else 'theme'
    return None


def application_metadata(outer, app):
    """Read only application Info keys; never serialize a backup/device plist."""
    expected = 'jp.konami.' + app
    result = {'app': app, 'bundle_id': expected, 'app_version': None,
              'bundle_id_basis': 'Explicit --app selection; not independently verified app metadata',
              'evidence': 'No unambiguous matching application Info.plist version evidence',
              'candidates': []}
    for item in outer.infolist():
        parts = PurePosixPath(item.filename).parts
        is_app_info = (len(parts) >= 2 and parts[-1] == 'Info.plist' and parts[-2].endswith('.app'))
        if item.is_dir() or not is_app_info:
            continue
        try:
            info = plistlib.loads(read_member(outer, item, maximum=1024 * 1024))
            if not isinstance(info, dict) or info.get('CFBundleIdentifier') != expected:
                continue
            allowed = {key: info[key] for key in ('CFBundleIdentifier', 'CFBundleShortVersionString',
                       'CFBundleVersion') if isinstance(info.get(key), str)}
            result['candidates'].append({'member': item.filename, **allowed})
        except (ValueError, OSError, plistlib.InvalidFileException, ExpatError, zipfile.BadZipFile):
            # An unrelated malformed application plist must not block song preservation.
            continue
    versions = {(c.get('CFBundleShortVersionString'), c.get('CFBundleVersion')) for c in result['candidates']}
    if len(versions) == 1:
        short, build = versions.pop()
        result.update(app_version=short or build, app_build=build,
                      bundle_id_basis='Matching CFBundleIdentifier in application Info.plist',
                      evidence='Only application bundle identifier/version keys were retained')
    return result


class Extractor:
    def __init__(self, source, out, app, *, jobs=1, ffprobe='ffprobe', mc=False, limit=None):
        self.source, self.out = Path(source), Path(out)
        if app not in ('jubeatplus', 'jukebeat'):
            raise ValueError('Specify jubeatplus or jukebeat; namespaces must remain separate')
        if jobs not in (1, 2) or (limit is not None and limit < 1):
            raise ValueError('jobs must be 1 or 2; limit must be positive')
        self.app, self.jobs, self.ffprobe, self.mc, self.limit = app, jobs, ffprobe, mc, limit
        self.decode = mobile.Decryptor()
        self.texture_decode = AlternateDecoder('texture')
        self.info3_decode = AlternateDecoder('infov3')
        self.local, self.lock, self.archives = threading.local(), threading.Lock(), []

    def outer(self):
        if not hasattr(self.local, 'archive'):
            self.local.archive = zipfile.ZipFile(self.source)
            with self.lock:
                self.archives.append(self.local.archive)
        return self.local.archive

    def song(self, item):
        sid = PurePosixPath(item.filename).stem
        song = self.out / 'songs' / sid
        row = {'schema': SCHEMA, 'id': sid, 'platform': 'mobile', 'layer': 'mobile',
               'package': self.app, 'source': {}, 'metadata': {}, 'members': [],
               'charts': {}, 'audio': {}, 'audio_status': {'bgm': 'missing', 'preview': 'missing'},
               'images': [], 'unknown_members': [], 'errors': []}
        error = row['errors'].append
        payloads = {}
        try:
            blob = read_member(self.outer(), item, MAX_JBT)
            original = self.out / 'raw' / (sid + '.jbt')
            dump(original, blob)
            row['source'] = {'archive_member': item.filename, 'jbt_path': rel(original, self.out),
                             'jbt_sha256': sha(blob), 'jbt_bytes': len(blob),
                             'zip_crc32': format(item.CRC, '08x'), 'zip_crc_verified': True}
            with zipfile.ZipFile(io.BytesIO(blob)) as inner:
                for entry in checked_infos(inner):
                    if entry.is_dir():
                        continue
                    name, relative = entry.filename, safe_member(entry.filename)
                    member = {'name': name, 'encrypted_bytes': entry.file_size}
                    row['members'].append(member)
                    try:
                        enc = read_member(inner, entry)
                        member['encrypted_sha256'] = sha(enc)
                        decoder = self.info3_decode if name == 'infov3' else self.decode
                        raw, status = decrypt_member(enc, name, decoder)
                        member['status'] = status
                        if raw is None:
                            row['unknown_members'].append(name)
                            continue
                        path = song / 'decrypted' / relative
                        dump(path, raw)
                        payloads[name] = raw
                        member.update(decrypted_path=rel(path, self.out), decrypted_bytes=len(raw),
                                      decrypted_sha256=sha(raw))
                    except Exception as exc:
                        member.update(status='failed', error=str(exc))
                        error({'stage': 'member', 'member': name, 'error': str(exc)})
                        if name in AUDIO:
                            row['audio_status'][AUDIO[name]] = 'failed'
                        if name in IMAGES:
                            error({'stage': 'image', 'member': name, 'error': 'Decryption failed; original retained in JBT'})
                        continue
                    if name in ('info', 'infov2', 'infov3') or raw.startswith(PLIST_MAGIC):
                        try:
                            payload = raw[4:] if name == 'infov3' and not raw.startswith(PLIST_MAGIC) else raw
                            meta = plistlib.loads(payload)
                            row['metadata'][name] = meta
                            base = song / 'metadata' / relative
                            dump(base.with_name(base.name + '.plist'), payload)
                            js(base.with_name(base.name + '.json'), meta)
                            # UID cannot be represented in XML; binary+tagged JSON remain complete.
                            try:
                                xml = plistlib.dumps(meta, fmt=plistlib.FMT_XML, sort_keys=False)
                                dump(base.with_name(base.name + '.xml'), xml)
                            except (TypeError, ValueError, OverflowError):
                                member['xml_status'] = 'not_representable_original_and_json_preserved'
                        except Exception as exc:
                            error({'stage': 'metadata', 'member': name, 'error': str(exc)})
                    if name.startswith('seq_'):
                        code = CODES.get(name[4:], name[4:].upper())
                        try:
                            # Avoid alias seq_bas/seq_bsc or nested chart-name output collisions.
                            if not re.fullmatch(r'[A-Z0-9_]+', code) or code in row['charts']:
                                raise ValueError('Unsafe or colliding chart difficulty name')
                            parsed = seq_events(raw)
                            binary = song / 'charts' / (code + '.seq')
                            dump(binary, raw)
                            events = song / 'charts' / (code + '.events.json')
                            js(events, parsed)
                            counts = parsed['commands']
                            row['charts'][code] = {
                                'raw': rel(binary, self.out), 'sha256': sha(raw),
                                'events_json': rel(events, self.out), 'events': parsed['record_count'],
                                'commands': counts, 'playable': bool(counts.get('PLAY', 0) or counts.get('LONG', 0)),
                                'notes': counts.get('PLAY', 0) + counts.get('LONG', 0),
                                'holds': counts.get('LONG', 0), 'unknown_types': parsed['unknown_types'],
                                'status': 'parsed', 'mc_exported': False}
                        except Exception as exc:
                            error({'stage': 'seq', 'member': name, 'error': str(exc)})
                    if name in AUDIO:
                        role = AUDIO[name]
                        audio_path = song / (role + audio_ext(raw))
                        dump(audio_path, raw)
                        audio = {'path': rel(audio_path, self.out), 'sha256': sha(raw), 'bytes': len(raw),
                                 'source_member': name, 'raw_decrypted': rel(path, self.out), 'status': 'failed'}
                        row['audio'][role], row['audio_status'][role] = audio, 'failed'
                        try:
                            audio['probe'] = probe(audio_path, self.ffprobe)
                            audio['status'] = row['audio_status'][role] = 'validated'
                        except Exception as exc:
                            error({'stage': 'audio', 'member': name, 'role': role, 'error': str(exc)})
                    if name in IMAGES or has_image(raw):
                        try:
                            row['images'].append({'member': name, **export_image(raw, song / 'images' / relative, self.out)})
                        except Exception as exc:
                            error({'stage': 'image', 'member': name, 'error': str(exc), 'raw_decrypted': rel(path, self.out)})
            meta = row['metadata'].get('infov3') or row['metadata'].get('infov2') or row['metadata'].get('info') or {}
            if not isinstance(meta, dict):
                error({'stage': 'metadata', 'error': 'Song metadata is not a dictionary'})
                meta = {}
            if not meta:
                error({'stage': 'metadata_missing', 'error': 'No parsed song metadata'})
            row['title'], row['artist'] = str(meta.get('Name') or sid), str(meta.get('Artist') or '')
            row['levels'] = {code: meta.get('Lv' + label) for code, label in zip(DIFFICULTIES, ('Bas', 'Adv', 'Ext'))}
            if self.mc:
                self.export_mc(row, song, payloads)
            for code in DIFFICULTIES:
                if code not in row['charts']:
                    error({'stage': 'chart_missing', 'difficulty': code, 'error': 'seq missing or not parsed'})
            for role in ('bgm', 'preview'):
                if row['audio_status'][role] == 'missing':
                    error({'stage': 'audio_missing', 'role': role, 'error': 'bgm/index member absent'})
        except Exception as exc:
            error({'stage': 'jbt', 'error': str(exc)})
        row['asset_completeness'] = classify(row)
        row['status'] = 'complete' if not row['errors'] else 'partial'
        js(song / 'info.json', row)
        return row

    def export_mc(self, row, song, payloads):
        cover = next((x['path'] for x in row['images'] if x['member'] == 'artwork'), None)
        cover = os.path.relpath(self.out / cover, song / 'charts') if cover else ''
        bgm = row['audio'].get('bgm', {}).get('path')
        bgm = os.path.relpath(self.out / bgm, song / 'charts') if bgm else ''
        for code, chart in row['charts'].items():
            if code not in DIFFICULTIES:
                continue
            source = next((n for n in payloads if n.startswith('seq_') and CODES.get(n[4:]) == code), None)
            if source is None:
                continue
            try:
                converted = mobile.parse_chart(payloads[source])
                content = mobile.write_chart_mc(converted['notes'], converted['timing'], row['title'],
                    row['artist'], {'BSC': 'bas', 'ADV': 'adv', 'EXT': 'ext'}[code],
                    row['levels'][code], cover, bgm)
                dump(song / 'charts' / (code + '.mc'), content)
                chart['mc_exported'] = True
            except Exception as exc:
                row['errors'].append({'stage': 'mc', 'member': source, 'error': str(exc)})

    def extras(self, outer, selected):
        rows, errors = [], []
        for item, destination, category in selected:
            record = {'source_member': item.filename, 'category': category, 'members': [], 'images': [], 'errors': []}
            rows.append(record)
            try:
                data = read_member(outer, item)
                target = self.out / 'extras' / 'originals' / destination
                dump(target, data)
                record.update(raw_path=rel(target, self.out), sha256=sha(data), bytes=len(data))
                if zipfile.is_zipfile(io.BytesIO(data)):
                    with zipfile.ZipFile(io.BytesIO(data)) as inner:
                        for entry in checked_infos(inner, maximum=5000):
                            if entry.is_dir():
                                continue
                            name = entry.filename
                            member = {'name': name}
                            record['members'].append(member)
                            if '__MACOSX' in PurePosixPath(name).parts or PurePosixPath(name).name == '.DS_Store':
                                member['status'] = 'archiver_residue_in_original_zip'
                                continue
                            try:
                                enc = read_member(inner, entry)
                                member.update(encrypted_bytes=len(enc), encrypted_sha256=sha(enc))
                                # Known plaintext first: arbitrary text can accidentally resemble an envelope.
                                if has_image(enc) or enc.startswith(PLIST_MAGIC):
                                    raw, status = enc, 'already_plain'
                                elif framed(enc):
                                    raw, status = self.texture_decode(enc), 'texture_bfcodec_decrypted'
                                else:
                                    raw, status = enc, 'opaque_or_plain_preserved'
                                if status == 'opaque_or_plain_preserved' and name.lower().endswith('.txt'):
                                    for encoding in ('utf-8-sig', 'cp932'):
                                        try:
                                            raw.decode(encoding)
                                            status, member['text_encoding'] = 'plain_text_preserved', encoding
                                            break
                                        except UnicodeDecodeError:
                                            pass
                                # Retain the complete ZIP name as a directory, avoiding x.zip / x.png collisions.
                                base = self.out / 'extras' / 'unpacked' / destination
                                dest = base / 'decrypted' / safe_member(name)
                                dump(dest, raw)
                                member.update(status=status, path=rel(dest, self.out), sha256=sha(raw), bytes=len(raw))
                                expected_image = (category == 'marker' and re.fullmatch(r'(?:ma|h[1-4])\d+', PurePosixPath(name).name)) or name.lower().endswith(('.png', '.jpg', '.jpeg', '.tex'))
                                if has_image(raw) or expected_image:
                                    try:
                                        record['images'].append({'member': name, **export_image(raw, base / 'images' / safe_member(name), self.out)})
                                    except Exception as exc:
                                        record['errors'].append({'stage': 'extras_image', 'member': name, 'error': str(exc), 'raw_decrypted': rel(dest, self.out)})
                                if raw.startswith(PLIST_MAGIC):
                                    js(dest.with_name(dest.name + '.json'), plistlib.loads(raw))
                            except Exception as exc:
                                member['status'], member['error'] = 'failed', str(exc)
                                record['errors'].append({'stage': 'extras_member', 'member': name, 'error': str(exc)})
                else:
                    raw = self.texture_decode(data) if framed(data) and not has_image(data) else data
                    plaintext = self.out / 'extras' / 'decrypted' / destination
                    dump(plaintext, raw)
                    record['decrypted_path'] = rel(plaintext, self.out)
                    try:
                        record['images'].append({'member': destination.name, **export_image(raw,
                            self.out / 'extras' / 'images' / destination, self.out)})
                    except Exception as exc:
                        record['errors'].append({'stage': 'extras_image', 'error': str(exc), 'raw_decrypted': rel(plaintext, self.out)})
            except Exception as exc:
                record['errors'].append({'stage': 'extras', 'error': str(exc)})
            errors.extend({'source': item.filename, **e} for e in record['errors'])
            record['status'] = 'complete' if not record['errors'] else 'partial'
        js(self.out / 'extras' / 'manifest.json', rows)
        js(self.out / 'extras' / 'errors.json', errors)
        return rows, errors

    def run(self):
        # A new destination prevents stale assets or cross-app accidental merging.
        if self.out.is_symlink() or (self.out.exists() and any(self.out.iterdir())):
            raise ValueError('Output must be a new or empty directory; use a separate directory per app/run')
        self.out.mkdir(parents=True, exist_ok=True)
        js(self.out / 'progress.json', {'finished': False, 'schema': SCHEMA})
        with zipfile.ZipFile(self.source) as outer:
            infos = outer.infolist()
            if len(infos) > mobile.MAX_ARCHIVE_ENTRIES:
                raise ValueError('Backup entry budget exceeded')
            app_metadata = application_metadata(outer, self.app)
            items = sorted((x for x in infos if select_song(x)), key=lambda x: x.header_offset)
            expected = len(items)
            if not expected:
                raise ValueError('No numeric Documents/*.jbt song packages found')
            ids = [PurePosixPath(x.filename).stem for x in items]
            if any(not re.fullmatch(r'\d+', sid) for sid in ids):
                raise ValueError('Unsupported nonnumeric JBT identifier; no song package was silently skipped')
            if len(ids) != len(set(ids)):
                raise ValueError('Duplicate song IDs in backup; do not combine app namespaces')
            selected, destinations = [], set()
            for item in infos:
                if item.is_dir():
                    continue
                match = extra_destination(item.filename)
                if match:
                    destination, category = match
                    key = destination.as_posix().casefold()
                    if key in destinations:
                        raise ValueError('Colliding installed-asset destinations')
                    destinations.add(key)
                    selected.append((item, destination, category))
            items = items[:self.limit] if self.limit is not None else items
            js(self.out / 'source_inventory.json', {
                'schema': SCHEMA, 'app': self.app, 'archive_bytes': self.source.stat().st_size,
                'expected_song_packages': expected, 'selected_song_packages': len(items),
                'songs': [{'member': i.filename, 'bytes': i.file_size, 'crc32': format(i.CRC, '08x')} for i in items],
                'selected_extra_members': [i.filename for i, _, _ in selected],
                'unselected_entry_count': len(infos) - expected - len(selected)})
            rows = []
            try:
                with ThreadPoolExecutor(max_workers=self.jobs) as pool:
                    for index, row in enumerate(pool.map(self.song, items), 1):
                        rows.append(row)
                        if index % 20 == 0 or index == len(items):
                            print(f'{self.app}: {index}/{len(items)} songs', flush=True)
                            js(self.out / 'progress.json', {'processed_song_packages': index,
                                'expected_song_packages': expected, 'finished': False})
            finally:
                for archive in self.archives:
                    archive.close()
            extra, extra_errors = self.extras(outer, selected)
        rows.sort(key=lambda r: r['id'])
        errors = [{'id': r['id'], **e} for r in rows for e in r['errors']] + extra_errors
        js(self.out / 'manifest.json', rows)
        js(self.out / 'errors.json', errors)
        js(self.out / 'app_version.json', app_metadata)
        stats = self.statistics(rows, extra, errors, expected)
        stats['app_version'] = app_metadata['app_version']
        js(self.out / 'stats.json', stats)
        js(self.out / 'progress.json', stats)
        zero = [{'id': r['id'], 'difficulty': c, 'raw': chart['raw'], 'sha256': chart['sha256'],
                 'commands': chart['commands']} for r in rows for c, chart in r['charts'].items() if not chart['playable']]
        js(self.out / 'zero-note-charts.json', zero)
        dump(self.out / '说明.txt', (
            f'移动版游戏素材完整导出\n应用版本：{app_metadata["app_version"] or "未知"}。plus / jukebeat 按输入参数与原始ID独立保存。\n'
            'raw为完整原JBT；decrypted为完整已识别明文；metadata保留原plist与带类型JSON。\n'
            'charts/*.seq与*.events.json保留300Hz完整记录和未知命令；MC仅为可选转换副本。\n'
            'bgm与preview分别来自原bgm/index资源；不转码，不裁切，ffprobe独立验证。\n'
            'extras仅导出安装的marker、主题、scImg与图标；不导出账户、成绩、设置、设备信息。\n'
            'songs_complete仅表示处理无错误，零note原谱可完整导出；不等于三份谱均可玩。\n'
            '原始五件套与三可玩谱加双音频分别见raw_five_assets_preserved/complete_five_assets。\n'
            '未知成员保留在原JBT，失败见errors.json；样本运行不表示整份备份完成。\n'
            f'歌曲 {len(rows)}/{expected}，原试听 {stats["preview_preserved"]}，错误 {len(errors)}。\n'
        ).encode('utf-8'))
        return stats

    def statistics(self, rows, extras, errors, expected):
        charts = [c for r in rows for c in r['charts'].values()]
        audio = [a for r in rows for a in r['audio'].values()]
        images = [i for r in rows for i in r['images']]
        counts = collections.Counter(i['member'] for i in images)
        codecs = collections.Counter(s.get('codec_name', 'unknown') for a in audio
            for s in a.get('probe', {}).get('streams', []) if s.get('codec_type') == 'audio')
        stats = {'schema': SCHEMA, 'platform': 'mobile', 'package': self.app, 'app_version': None,
            'expected_song_packages': expected, 'processed_song_packages': len(rows),
            'song_directories': len(rows), 'songs_complete': sum(r['status'] == 'complete' for r in rows),
            'songs_complete_meaning': 'Source resources processed without errors; does not assert three playable charts',
            'chart_files': len(charts), 'playable_charts': sum(c['playable'] for c in charts),
            'zero_note_charts': sum(not c['playable'] for c in charts),
            'mc_files': sum(c['mc_exported'] for c in charts),
            'image_failures': sum(e['stage'] in ('image', 'extras_image') for e in errors),
            'notes': sum(c['notes'] for c in charts), 'holds': sum(c['holds'] for c in charts),
            'images_decoded': len(images), 'image_variant_counts': dict(counts),
            'metadata_variant_counts': dict(collections.Counter(k for r in rows for k in r['metadata'])),
            'audio_validated': sum(a['status'] == 'validated' for a in audio), 'audio_codec_counts': dict(codecs),
            'unknown_jbt_members': sum(len(r['unknown_members']) for r in rows),
            'raw_jbt_bytes': sum(r['source'].get('jbt_bytes', 0) for r in rows),
            'extras_images_decoded': sum(len(e['images']) for e in extras),
            'marker_frame_images': sum(len(e['images']) for e in extras if e['category'] == 'marker' and e.get('members')),
            'theme_images_decoded': sum(len(e['images']) for e in extras if e['category'] == 'theme'),
            'scimg_exported': sum(len(e['images']) for e in extras if e['category'] == 'scimg'),
            'errors': len(errors), 'finished': True, 'limited_sample': self.limit is not None,
            'status': 'complete' if len(rows) == expected and not errors and self.limit is None else 'partial'}
        for role in ('bgm', 'preview'):
            stats[role + '_preserved'] = sum(role in r['audio'] for r in rows)
            for status in ('validated', 'missing', 'failed'):
                stats[role + '_' + status] = sum(r['audio_status'][role] == status for r in rows)
        for key in ('raw_five_assets_preserved', 'complete_five_assets', 'three_raw_charts_present', 'three_playable_charts'):
            stats[key] = sum(r['asset_completeness'][key] for r in rows)
        stats['unknown_extra_members_preserved'] = sum(m.get('status') == 'opaque_or_plain_preserved'
            for e in extras for m in e['members'])
        return stats


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='ZIP-based .adbk backup')
    parser.add_argument('--out', type=Path, required=True, help='New/empty output directory')
    parser.add_argument('--app', choices=('jubeatplus', 'jukebeat'), required=True)
    parser.add_argument('--jobs', type=int, choices=(1, 2), default=1)
    parser.add_argument('--ffprobe', default=shutil.which('ffprobe') or 'ffprobe')
    parser.add_argument('--mc', action='store_true', help='Also export optional quantized MC copies')
    parser.add_argument('--limit', type=int, help='Process first N songs; marks result as limited sample')
    args = parser.parse_args(argv)
    try:
        try:
            from PIL import Image  # noqa: F401
        except ImportError as exc:
            raise RuntimeError('Pillow is required: python3 -m pip install Pillow') from exc
        if not shutil.which(args.ffprobe):
            raise ValueError('ffprobe is required; use --ffprobe PATH')
        stats = Extractor(args.input, args.out, args.app, jobs=args.jobs,
                          ffprobe=args.ffprobe, mc=args.mc, limit=args.limit).run()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0 if stats['status'] == 'complete' else 2
    except (ValueError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        parser.exit(1, 'Error: ' + str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
