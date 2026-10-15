#!/usr/bin/env python3
"""Offline export of the verified T44 data layout; never run game binaries."""
from __future__ import annotations

import argparse
import collections
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import xml.etree.ElementTree as ET

from arcade import import_ifs as arcade
from assets.eve import parse_eve_chart
from assets.s3p import extract_s3p

KNOWN_COMMANDS = {'PLAY', 'LONG', 'TEMPO', 'MEASURE', 'HAKU', 'END'}


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def sha(path, algorithm='sha256'):
    result = hashlib.new(algorithm)
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def link(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        raise FileExistsError(str(dst))
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def xmlobj(node):
    if node is None:
        return None
    return {'tag': node.tag, 'attributes': dict(node.attrib), 'text': node.text,
            'tail': node.tail, 'children': [xmlobj(child) for child in node]}


def exact_eve_events(raw):
    """Lossless field view: retain original values, all columns and rejected lines.

    The original EVE bytes/SHA remain authoritative for encoding and line endings.
    This parser does not reuse the MC adapter, which intentionally discards some
    guide values and malformed rows while constructing playable notes.
    """
    encoding, decoding_error = None, None
    for candidate in ('utf-8-sig', 'cp932'):
        try:
            text = raw.decode(candidate)
            encoding = candidate
            break
        except UnicodeDecodeError:
            pass
    else:
        text = raw.decode('cp932', errors='replace')
        encoding, decoding_error = 'cp932-with-replacement', 'Cannot decode strictly as UTF-8 or CP932; original EVE bytes retained'
    lines, events, unparsed = [], [], []
    for number, original in enumerate(text.splitlines(), 1):
        fields = original.split(',')
        stripped = [field.strip() for field in fields]
        row = {'line_number': number, 'raw_line': original, 'fields': fields}
        lines.append(row)
        if not original.strip():
            row['status'] = 'blank'
            continue
        try:
            if len(stripped) < 2 or not stripped[1]:
                raise ValueError('Missing command column')
            tick = int(stripped[0])
            values = stripped[2:]
            value = None
            if values:
                try:
                    value = int(values[0])
                except ValueError:
                    value = values[0]
            try:
                seconds = tick / 300
            except OverflowError:
                seconds = None
            event = {'line_number': number, 'tick': tick, 'seconds': seconds,
                     'command': stripped[1], 'value': value, 'values': values,
                     'fields': fields, 'raw_line': original}
            events.append(event)
            row['status'] = 'event'
        except ValueError as exc:
            row.update(status='unparsed', error=str(exc))
            unparsed.append(dict(row))
    result = {'schema': 2, 'tick_hz': 300, 'source_sha256': hashlib.sha256(raw).hexdigest(),
              'raw_text_encoding': encoding, 'events': events, 'lines': lines,
              'unparsed_lines': unparsed,
              'unknown_commands': sorted({e['command'] for e in events} - KNOWN_COMMANDS)}
    if decoding_error:
        result['decoding_error'] = decoding_error
    return result


def metadata_path(raw, name):
    # Historical metadata uses a leading slash for paths relative to its data root.
    text = str(name).lstrip('/')
    path = PurePosixPath(text)
    if (not text or '\\' in text or ':' in text or path.is_absolute()
            or any(part in ('', '.', '..') for part in text.split('/'))):
        raise ValueError('Unsafe embedded metadata path')
    result = raw.joinpath(*path.parts)
    if raw.resolve() not in result.resolve().parents or result.is_symlink():
        raise ValueError('Embedded metadata path escapes raw snapshot')
    return result


def checked_ifs_extract(source, output, depth=0):
    """Preflight every path before asking ifstools to convert known graphics."""
    from ifstools import IFS
    if depth > 8:
        raise ValueError('Nested IFS depth budget exceeded')
    if output.exists():
        raise FileExistsError(str(output))
    obj = IFS(str(source))
    try:
        seen = set()
        for node in obj.tree.all_files:
            name = node.full_path.replace('\\', '/')
            relative = PurePosixPath(name)
            if (not name or relative.is_absolute() or ':' in name
                    or any(p in ('', '.', '..') for p in name.split('/'))):
                raise ValueError('Unsafe IFS member path')
            key = name.casefold()
            if key == 'ifs_manifest.xml':
                raise ValueError('IFS member collides with generated manifest')
            if key in seen:
                raise ValueError('Case-colliding IFS member paths')
            seen.add(key)
            if output.resolve() not in output.joinpath(*relative.parts).resolve().parents:
                raise ValueError('IFS output outside destination')
        for node in obj.tree.all_folders:
            name = node.full_path.replace('\\', '/')
            if name:
                relative = PurePosixPath(name)
                if relative.is_absolute() or ':' in name or '..' in relative.parts:
                    raise ValueError('Unsafe IFS folder path')
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            obj.extract(path=str(output), progress=False, extract_manifest=True, recurse=False)
    finally:
        obj.close()
    # Nested containers receive the same checks; the library's recursive mode
    # would bypass this preflight. Their original bytes remain in raw/ as well.
    nested = [p for p in output.rglob('*') if p.is_file() and p.suffix.lower() == '.ifs']
    for child in nested:
        checked_ifs_extract(child, child.with_name(child.stem + '_ifs'), depth + 1)


class T44Exporter:
    def __init__(self, source, out):
        self.source, self.out = Path(source), Path(out)
        self.raw, self.songs, self.extra = self.out / 'raw', self.out / 'songs', self.out / 'extras'
        self.errors, self.warnings, self.manifest = [], [], []
        self.ffmpeg, self.ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')

    def error(self, stage, source, exc, **fields):
        try:
            source = str(Path(source).relative_to(self.raw))
        except ValueError:
            source = str(source)
        self.errors.append({'stage': stage, 'source': source, 'error': str(exc), **fields})

    def probe(self, path):
        result = subprocess.run([self.ffprobe, '-v', 'error', '-show_format', '-show_streams',
                                 '-of', 'json', str(path)], capture_output=True, timeout=120)
        if result.returncode:
            raise ValueError(result.stderr.decode(errors='replace')[:4000])
        return json.loads(result.stdout)

    def decode(self, source, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run([self.ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'warning',
                                 '-n', '-i', str(source), '-map', '0:a:0', '-c:a', 'flac',
                                 str(destination)], capture_output=True, timeout=600)
        if result.returncode:
            raise ValueError(result.stderr.decode(errors='replace')[:4000])
        if result.stderr:
            self.warnings.append({'source': str(source.relative_to(self.raw)),
                                  'message': result.stderr.decode(errors='replace')[:4000]})
        info = self.probe(destination)
        if not any(s.get('codec_type') == 'audio' for s in info.get('streams', [])):
            raise ValueError('Decoded FLAC has no audio stream')
        return info

    def snapshot(self):
        if self.source.is_symlink() or not self.source.is_dir():
            raise ValueError('Input must be a real data directory')
        if self.out.is_symlink():
            raise ValueError('Output symbolic links are unsupported')
        self.source, self.out = self.source.resolve(), self.out.resolve()
        self.raw, self.songs, self.extra = self.out / 'raw', self.out / 'songs', self.out / 'extras'
        if self.out.exists() or self.out.is_symlink():
            raise ValueError('Use a new output directory')
        if self.source == self.out or self.source in self.out.parents or self.out in self.source.parents:
            raise ValueError('Input and output must be separate, non-nested directories')
        if not self.ffmpeg or not self.ffprobe:
            raise ValueError('ffmpeg and ffprobe must be on PATH')
        paths = sorted(self.source.rglob('*'))
        for path in paths:
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError('Input symbolic links/special files are unsupported')
        if not (self.source / 'music/music_info.xml').is_file():
            raise ValueError('Input must contain music/music_info.xml')
        self.raw.mkdir(parents=True)
        self.songs.mkdir()
        self.extra.mkdir()
        dump(self.out / 'progress.json', {'finished': False, 'stage': 'raw_snapshot'})
        for source in paths:
            destination = self.raw / source.relative_to(self.source)
            if source.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                link(source, destination)

    def song(self, source, node):
        sid, target = source.name, self.songs / source.name
        target.mkdir()
        fields = {child.tag: (child.text or '').strip() for child in node} if node is not None else {}
        song = {'id': sid, 'title': fields.get('music_name'), 'artist': fields.get('artist_name'),
                'source': str(source.relative_to(self.raw)), 'metadata': xmlobj(node),
                'assets': [], 'charts': {}, 'audio': {}, 'images': [], 'videos': [],
                'special': node is None}
        before = len(self.errors)
        if node is not None:
            (target / 'metadata.xml').write_bytes(ET.tostring(node, encoding='utf-8', xml_declaration=True))
        for src in sorted(source.rglob('*')):
            if src.is_file():
                link(src, target / 'original' / src.relative_to(source))
                song['assets'].append({'path': str(src.relative_to(self.raw)), 'size': src.stat().st_size, 'sha256': sha(src)})
        for kind, name in (('bgm', 'bgm.flac'), ('idx', 'preview.flac')):
            src = source / (sid + kind + '.s3v')
            if src.is_file():
                try:
                    info = self.decode(src, target / name)
                    song['audio'][kind] = {'output': str((target / name).relative_to(self.out)),
                                           'source_probe': self.probe(src), 'output_probe': info, 'status': 'decoded'}
                except Exception as exc:
                    self.error('audio', src, exc)
        for src in sorted(source.glob('*.png')):
            try:
                from PIL import Image
                with Image.open(src) as image:
                    image.verify()
                role = 'cover.png' if 'big' in src.name else 'cover_small.png'
                destination = target / role
                if not destination.exists():
                    link(src, destination)
                song['images'].append({'source': str(src.relative_to(self.raw)),
                                       'preferred': str(destination.relative_to(self.out)), 'status': 'validated'})
            except Exception as exc:
                self.error('image', src, exc)
        for src in sorted(source.glob('*.wmv')):
            row = {'source': str(src.relative_to(self.raw)), 'status': 'failed'}
            song['videos'].append(row)
            try:
                info = self.probe(src)
                dst = target / 'videos' / (src.stem + '.mp4')
                dst.parent.mkdir(exist_ok=True)
                result = subprocess.run([self.ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'warning',
                    '-n', '-i', str(src), '-c:v', 'libx264', '-crf', '18', '-preset', 'fast', '-an', str(dst)],
                    capture_output=True, timeout=600)
                if result.returncode:
                    raise ValueError(result.stderr.decode(errors='replace')[:4000])
                output_info = self.probe(dst)
                if not any(s.get('codec_type') == 'video' for s in output_info.get('streams', [])):
                    raise ValueError('Converted preview has no video stream')
                row.update(probe=info, output_probe=output_info, output=str(dst.relative_to(self.out)), status='decoded')
                song.setdefault('video', row)
            except Exception as exc:
                row['error'] = str(exc)
                self.error('video', src, exc)
        for eve in sorted(source.glob('*.eve')):
            code, raw = eve.stem.upper(), eve.read_bytes()
            events = exact_eve_events(raw)
            dump(target / 'charts' / (eve.stem + '.events.json'), events)
            chart = {'raw': str((target / 'original' / eve.name).relative_to(self.out)),
                     'sha256': sha(eve), 'events': len(events['events']),
                     'commands': dict(collections.Counter(e['command'] for e in events['events'])),
                     'unparsed_lines': len(events['unparsed_lines']), 'status': 'raw_events_preserved', 'mc_exported': False}
            song['charts'][code] = chart
            if events['unparsed_lines'] or events.get('decoding_error'):
                self.error('eve_unparsed', eve, 'Some lines/encoding require inspection; full raw and line records retained',
                           unparsed_lines=len(events['unparsed_lines']))
            try:
                notes, timing, offset = parse_eve_chart(raw, code, eve.name)
                levelraw = fields.get('level_' + eve.stem)
                level = str(int(levelraw) / 10) if levelraw else '?'
                mc = {'meta': {'mode': 4, 'version': f'{code} Lv{level}', 'creator': 'jubeat',
                      'song': {'title': song['title'] or sid, 'artist': song['artist'] or ''}, 'level': level,
                      'cover': '../cover.png' if (target / 'cover.png').is_file() else ''},
                      'time': [{'beat': arcade.beat_tuple(b), 'bpm': float(bpm)} for b, bpm in timing], 'note': [],
                      'conversion': {'tick_hz': 300, 'beat_grid': 48, 'raw_eve_retained': True}}
                if 'bgm' in song['audio']:
                    mc['note'].append({'beat': [0, 0, 1], 'sound': '../bgm.flac', 'type': 1, 'offset': 0, 'vol': 100})
                for beat, index, endbeat, endindex in notes:
                    note = {'beat': arcade.beat_tuple(beat), 'index': index}
                    if endbeat is not None:
                        note.update(endbeat=arcade.beat_tuple(endbeat), endindex=endindex)
                    mc['note'].append(note)
                dump(target / 'charts' / (eve.stem + '.mc'), mc)
                chart.update(notes=len(notes), holds=sum(n[2] is not None for n in notes), level_raw=levelraw,
                             level_interpretation=level, status='parsed', mc_exported=True)
            except Exception as exc:
                chart.update(status='conversion_error', error=str(exc))
                self.error('chart', eve, exc)
        song['status'] = 'complete' if len(self.errors) == before else 'partial'
        dump(target / 'info.json', song)
        self.manifest.append(song)

    def run(self):
        self.snapshot()
        raw_files = [p for p in sorted(self.raw.rglob('*')) if p.is_file()]
        raw_manifest = [{'path': str(p.relative_to(self.raw)), 'size': p.stat().st_size, 'sha256': sha(p)} for p in raw_files]
        dump(self.out / 'raw_manifest.json', raw_manifest)
        metadata, xml = {}, None
        try:
            xml = ET.parse(self.raw / 'music/music_info.xml').getroot()
            for node in xml.findall('./body/data'):
                sid = str(node.findtext('music_id'))
                if sid in metadata:
                    self.error('metadata', 'music/music_info.xml', 'Duplicate music_id: ' + sid)
                else:
                    metadata[sid] = node
            shutil.copy2(self.raw / 'music/music_info.xml', self.extra / 'music_info.xml')
            dump(self.extra / 'music_info.json', xmlobj(xml))
        except Exception as exc:
            self.error('metadata', 'music/music_info.xml', exc)
        for source in sorted((self.raw / 'music').iterdir()):
            if source.is_dir():
                try:
                    self.song(source, metadata.get(source.name))
                except Exception as exc:
                    self.error('song', source, exc)
        ifs_results, sound_results = [], []
        for src in (p for p in raw_files if p.suffix.lower() == '.ifs'):
            dst = self.extra / 'ifs' / src.relative_to(self.raw).with_suffix('')
            record = {'source': str(src.relative_to(self.raw)), 'output': str(dst.relative_to(self.out))}
            try:
                checked_ifs_extract(src, dst)
                record.update(files=sum(p.is_file() for p in dst.rglob('*')), status='extracted')
            except Exception as exc:
                record.update(status='failed', error=str(exc))
                self.error('IFS', src, exc)
            ifs_results.append(record)
        for src in (p for p in raw_files if p.suffix.lower() == '.s3p'):
            dst = self.extra / 'sound' / src.relative_to(self.raw).with_suffix('')
            try:
                result = extract_s3p(src, dst, decode=True)
                enum, labels = src.with_suffix('.enum'), []
                if enum.is_file():
                    link(enum, dst / enum.name)
                    labels = [line.strip().rstrip(',') for line in enum.read_text(errors='replace').splitlines()[1:]
                              if line.strip() and not line.strip().startswith('}')]
                for member in result['records']:
                    record = {'source': str(src.relative_to(self.raw)), **member}
                    index = member['index']
                    record['label'] = labels[index] if index < len(labels) else None
                    for key in ('wrapped', 'asf', 'flac'):
                        if member.get(key):
                            record[key] = str((dst / member[key]).relative_to(self.out))
                    sound_results.append(record)
                for error in result['errors']:
                    self.error('S3P', src, error.get('error', str(error)), index=error.get('index'))
            except Exception as exc:
                self.error('S3P', src, exc)
        checks = []
        embedded = self.raw / '__metadata.metatxt'
        if embedded.is_file():
            try:
                records = json.loads(embedded.read_text())['files']
                if not isinstance(records, list):
                    raise ValueError('Embedded metadata files must be a list')
                for record in records:
                    if not isinstance(record, dict):
                        self.error('embedded_metadata', embedded, 'Embedded metadata record must be an object')
                        continue
                    check = {'path': record.get('path'), 'exists': False, 'size_ok': False, 'sha1_ok': False}
                    checks.append(check)
                    try:
                        path = metadata_path(self.raw, record['path'])
                        check.update(exists=path.is_file(), size_ok=path.is_file() and path.stat().st_size == record['size'],
                                     sha1_ok=path.is_file() and sha(path, 'sha1') == record['sha1'])
                        if not all(check[key] for key in ('exists', 'size_ok', 'sha1_ok')):
                            raise ValueError('Embedded metadata file/size/SHA1 mismatch')
                    except Exception as exc:
                        check['error'] = str(exc)
                        self.error('embedded_metadata', embedded, exc, declared_path=record.get('path'))
            except Exception as exc:
                self.error('embedded_metadata', embedded, exc)
        dump(self.extra / 'embedded-metadata-verification.json', checks)
        dump(self.extra / 'ifs-results.json', ifs_results)
        dump(self.extra / 'sound-results.json', sound_results)
        dump(self.extra / 'warnings.json', self.warnings)
        dump(self.out / 'manifest.json', self.manifest)
        dump(self.out / 'errors.json', self.errors)
        stats = {'version': 'T44 family', 'metadata_songs': len(metadata), 'song_directories': len(self.manifest),
            'songs_with_charts': sum(bool(s['charts']) for s in self.manifest),
            'charts_parsed': sum(c.get('mc_exported', False) for s in self.manifest for c in s['charts'].values()),
            'notes': sum(c.get('notes', 0) for s in self.manifest for c in s['charts'].values()),
            'holds': sum(c.get('holds', 0) for s in self.manifest for c in s['charts'].values()),
            'song_audio_decoded': sum(len(s['audio']) for s in self.manifest),
            'covers': len(list(self.songs.glob('*/cover*.png'))),
            'special_video_directories': sum('video' in s for s in self.manifest),
            'ifs_containers': len(ifs_results), 'ifs_extracted': sum(r['status'] == 'extracted' for r in ifs_results),
            'sound_bank_audio_decoded': sum(bool(r.get('flac')) and r.get('status') != 'decode_failed' for r in sound_results),
            'raw_files': len(raw_manifest), 'raw_bytes': sum(r['size'] for r in raw_manifest),
            'embedded_metadata_records': len(checks),
            'embedded_metadata_exact_sha1_matches': sum(r['sha1_ok'] for r in checks),
            'errors': len(self.errors), 'warnings': len(self.warnings), 'finished': True,
            'status': 'partial' if self.errors else 'complete',
            'status_meaning': 'All queued source resources processed; not a claim of three playable charts per music directory'}
        dump(self.out / 'stats.json', stats)
        dump(self.out / 'progress.json', stats)
        dump(self.extra / 'unique-content.json', {
            'notes': ['Complete raw snapshot retained by hardlink/cross-filesystem copy.',
                      'Exact EVE field/line records are separate from quantized MC.',
                      'S3P originals, S3V wrappers, ASF payloads and optional FLAC remain distinct.',
                      'Non-metadata music directories are retained without assuming song identity.'],
            'non_metadata_music_directories': [s['id'] for s in self.manifest if s['special']],
            'schema_root': dict(xml.attrib) if xml is not None else None,
            'header': xmlobj(xml.find('header')) if xml is not None else None})
        print(json.dumps(stats, ensure_ascii=False, indent=2), flush=True)
        return 2 if self.errors else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='T44 data directory containing music/music_info.xml')
    parser.add_argument('--out', type=Path, required=True, help='New independent output directory')
    args = parser.parse_args(argv)
    try:
        return T44Exporter(args.input, args.out).run()
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, 'Error: ' + str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
