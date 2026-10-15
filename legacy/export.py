"""Read-only raw preservation and explicit per-resource layer selection."""
from __future__ import annotations
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil

from .pkfs import MAX_ENTRY_BYTES, XorCodec, candidate_index, decode_payload, parse_index

ROLES = {'BSC.eve', 'ADV.eve', 'EXT.eve', 'bgm.bin', 'preview.bin'}


def sha256(blob):
    return hashlib.sha256(blob).hexdigest()


def safe_relative(name, leading_slash=False):
    """Accept PKFS's canonical leading slash, but never filesystem escapes."""
    if leading_slash and name.startswith('/'):
        name = name[1:]
    if not name or '\\' in name or ':' in name or '\x00' in name:
        raise ValueError('Unsafe resource path')
    parts = name.split('/')
    if any(part in {'', '.', '..'} for part in parts):
        raise ValueError('Unsafe resource path segment')
    path = PurePosixPath(name)
    if path.is_absolute():
        raise ValueError('Absolute resource path is not allowed')
    return Path(*parts)


def destination(root, relative):
    name = relative.as_posix() if isinstance(relative, Path) else str(relative)
    path = root / safe_relative(name)
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Output symlink or path escape')
    return path


def preserve_bytes(path, blob):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('Refusing to write through an output symlink')
    if path.exists():
        if not path.is_file() or path.read_bytes() != blob:
            raise ValueError('Existing output differs; choose a clean output directory')
        return False
    with path.open('xb') as stream:
        stream.write(blob)
    return True


def preserve_file(source, target):
    if source.is_symlink():
        raise ValueError('Source symlinks are not followed')
    target.parent.mkdir(parents=True, exist_ok=True)
    def digest(path):
        result = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                result.update(block)
        return result.hexdigest()
    source_hash = digest(source)
    if target.is_symlink():
        raise ValueError('Refusing to write through an output symlink')
    if target.exists():
        if not target.is_file() or source.stat().st_size != target.stat().st_size or digest(target) != source_hash:
            raise ValueError('Existing output differs; choose a clean output directory')
    else:
        with source.open('rb') as reader, target.open('xb') as writer:
            shutil.copyfileobj(reader, writer, 1024 * 1024)
    return source_hash


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('Refusing to replace an output symlink')
    temporary = path.with_suffix(path.suffix + '.tmp')
    if temporary.is_symlink():
        raise ValueError('Refusing a temporary output symlink')
    temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def validate_roots(inputs, output):
    if output.is_symlink():
        raise ValueError('Output root cannot be a symlink')
    output = output.resolve()
    for source in inputs:
        if source.is_symlink() or not source.is_dir():
            raise ValueError('Input must be an existing directory, not a symlink')
        resolved = source.resolve()
        if output.is_relative_to(resolved) or resolved.is_relative_to(output):
            raise ValueError('Input and output roots must not contain each other')
    output.mkdir(parents=True, exist_ok=True)
    return output


def inventory(root):
    """Snapshot first, excluding symlinks including symlinked directories."""
    import os
    files = []
    for folder, dirs, names in os.walk(root, followlinks=False):
        base = Path(folder)
        for name in dirs:
            if (base / name).is_symlink():
                raise ValueError('Source directory contains a symlink')
        for name in names:
            path = base / name
            if path.is_symlink():
                raise ValueError('Source directory contains a symlink')
            if not path.is_file():
                raise ValueError('Source contains a non-regular file')
            files.append(path)
    return sorted(files)


def detect_kind(blob):
    if blob.startswith(b'BMP\0'):
        return 'audio_bmp'
    if blob.startswith(b'PXET'):
        return 'texture_texbin'
    if blob.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if blob.startswith(b'\xa0\xa1'):
        return 'ifs'
    if b'MEASURE' in blob[:1000] and b'TEMPO' in blob[:2000]:
        return 'chart_eve'
    if blob.lstrip().startswith((b'<?xml', b'<music_data')):
        return 'xml'
    return 'binary'


def extract_pak(source, output, candidates=(), *, encoding='utf-8', native='auto',
                max_entry_bytes=MAX_ENTRY_BYTES):
    """Retain packinfo/PAK and extract every bounded entry, named or unresolved."""
    source = Path(source)
    output = validate_roots([source], Path(output))
    if max_entry_bytes <= 0:
        raise ValueError('max_entry_bytes must be positive')
    files = inventory(source)
    indexes = [p for p in files if p.name == 'packinfo.bin']
    if not indexes:
        raise ValueError('No packinfo.bin found below input root')
    names = candidate_index(candidates, encoding)
    # Validate caller-provided names before any name is used as a filesystem path.
    for values in names.values():
        for name in values:
            safe_relative(name, leading_slash=True)
    codec = XorCodec(native)
    records, errors, raw_sources = [], [], {}
    # Preserve even unreferenced PAKs before examining entry bounds. An index may
    # be incomplete; absence of a reference is not permission to discard a pack.
    for file in files:
        if file.name == 'packinfo.bin' or file.suffix.lower() == '.pak':
            relative = file.relative_to(source)
            target = destination(output, Path('raw') / relative)
            raw_sources[str(relative)] = {'path': str(target.relative_to(output)),
                                          'sha256': preserve_file(file, target)}
    for index in indexes:
        relative = index.relative_to(source)
        if index.stat().st_size > 128 * 1024 * 1024:
            raise ValueError('packinfo exceeds 128 MiB index allocation limit')
        index_bytes = index.read_bytes()
        target = destination(output, Path('raw') / relative)
        index_hash = preserve_file(index, target)
        raw_sources[str(relative)] = {'path': str(target.relative_to(output)), 'sha256': index_hash}
        try:
            rows = parse_index(index_bytes)
        except ValueError as exc:
            errors.append({'source': relative.as_posix(), 'error': str(exc)})
            continue
        for row in rows:
            rec = dict(row, index_source=relative.as_posix())
            try:
                pak = index.parent / f"d{row['pack'] // 30:03}" / f"pack{row['pack']:04}.pak"
                if not pak.resolve().is_relative_to(source.resolve()) or pak.is_symlink():
                    raise ValueError('PAK source path escape')
                pak_relative = pak.relative_to(source)
                rec['source'] = pak_relative.as_posix()
                if not pak.is_file():
                    raise ValueError('Referenced PAK is missing')
                if str(pak_relative) not in raw_sources:
                    target = destination(output, Path('raw') / pak_relative)
                    raw_sources[str(pak_relative)] = {'path': str(target.relative_to(output)),
                                                        'sha256': preserve_file(pak, target)}
                if row['size'] > max_entry_bytes:
                    raise ValueError('PAK entry exceeds configured allocation limit')
                if row['offset'] + row['size'] > pak.stat().st_size:
                    raise ValueError('PAK slice is out of bounds')
                with pak.open('rb') as stream:
                    stream.seek(row['offset'])
                    stored = stream.read(row['size'])
                if len(stored) != row['size']:
                    raise ValueError('PAK slice became short while reading')
                plain, transform, verified = decode_payload(stored, row, codec)
                matches = names.get((row['crc32'], row['crc16']), [])
                name = matches[0] if len(matches) == 1 and verified else None
                kind = detect_kind(plain) if verified else 'stored_unverified'
                if name:
                    rel = Path('payloads') / relative.parent / safe_relative(name, leading_slash=True)
                else:
                    suffix = {'audio_bmp': '.bmpaudio', 'texture_texbin': '.texbin',
                              'chart_eve': '.eve', 'xml': '.xml', 'png': '.png'}.get(kind, '.bin')
                    if not verified:
                        suffix = '.stored'
                    rel = Path('payloads') / relative.parent / '_unnamed' / (
                        f"{row['record']:06d}_{row['crc32']:08x}_{row['crc16']:04x}" + suffix)
                preserve_bytes(destination(output, rel), plain)
                rec.update(path=rel.as_posix(), kind=kind, transform=transform,
                           md5_verified=verified, stored_slice_sha256=sha256(stored),
                           sha256=sha256(plain), recovered_name=name,
                           candidate_names=matches, candidate_double_hash_verified=bool(name))
                if not verified:
                    errors.append({'source': rec['source'], 'record': row['record'],
                                   'error': 'Stored MD5 does not match; preserved stored bytes, not assumed plaintext'})
                if len(matches) > 1:
                    rec['name_status'] = 'ambiguous_double_hash_collision'
            except (OSError, ValueError) as exc:
                rec['error'] = str(exc)
                errors.append({'source': relative.as_posix(), 'record': row['record'], 'error': str(exc)})
            records.append(rec)
    stats = {'format': 'legacy_pkfs', 'entries': len(records),
             'md5_verified': sum(bool(r.get('md5_verified')) for r in records),
             'recovered_names': sum(bool(r.get('recovered_name')) for r in records),
             'historical_tail_matches': sum(r.get('transform') == 'pkfs_rolling_xor_historical_tail' for r in records),
             'native_acceleration': codec.native is not None, 'errors': len(errors),
             'status': 'complete' if not errors else 'complete_with_errors'}
    manifest = {'schema_version': 1, 'stats': stats, 'records': records,
                'raw_sources': raw_sources, 'warnings': [codec.warning] if codec.warning else [],
                'notes': ['Only stored MD5-verified transforms are called plaintext.',
                          'Filename recovery requires both CRC32 and CRC16/X25; unknown names remain unresolved.',
                          'No game version or official catalog membership is inferred from filenames.']}
    write_json(output / 'manifest.json', manifest)
    write_json(output / 'stats.json', stats)
    write_json(output / 'errors.json', errors)
    return manifest


def chart_events(blob):
    """Preserve integer 300 Hz ticks and unknown commands; never beat-quantize."""
    events, errors = [], []
    # EVE text in observed early assets is ASCII-compatible CP932. Keep raw bytes
    # separately; an undecodable line must be explicit, never silently repaired.
    for number, line in enumerate(blob.splitlines(), 1):
        try:
            text = line.decode('cp932', errors='strict').rstrip('\x00').strip()
            if not text:
                continue
            fields = text.split(',')
            if len(fields) < 3 or not fields[1].strip():
                raise ValueError('Expected tick, command, integer value')
            event = {'tick': int(fields[0]), 'command': fields[1].strip(), 'value': int(fields[2])}
            if len(fields) > 3:
                event['extra_columns'] = fields[3:]
            events.append(event)
        except (UnicodeError, ValueError) as exc:
            errors.append({'line': number, 'raw_hex': line.hex(), 'error': str(exc)})
    return {'tick_hz': 300, 'events': events,
            'statistics': dict(Counter(e['command'] for e in events)), 'unparsed_lines': errors}


def music_role(name, container_id=None):
    leaf = PurePosixPath(name).name.lower()
    match = re.fullmatch(r'(\d{8})([abe]\.eve|bgm\.bin|idx\.bin)', leaf)
    if match:
        song, suffix = match.groups()
        return song, {'a.eve': 'ADV.eve', 'b.eve': 'BSC.eve', 'e.eve': 'EXT.eve',
                      'bgm.bin': 'bgm.bin', 'idx.bin': 'preview.bin'}[suffix]
    if container_id:
        if leaf in {'bsc.eve', 'adv.eve', 'ext.eve'}:
            return container_id, leaf[:3].upper() + '.eve'
        if leaf in {'bgm.bin', 'bgm.bmp'}:
            return container_id, 'bgm.bin'
        if leaf in {'idx.bin', 'idx.bmp', 'preview.bin'}:
            return container_id, 'preview.bin'
    return None


def export_layers(layers, output, *, extract_ifs=True, decode_audio=False,
                  decode_textures=False, ifs_factory=None, max_member_bytes=MAX_ENTRY_BYTES):
    """Copy raw assets, export IFS payloads and select layers without overwriting.

    layers is an ordered list of (name, directory); the last layer wins per role.
    The IFS dependency is injected in tests and imported only when needed.
    """
    if not layers or len({name for name, _ in layers}) != len(layers):
        raise ValueError('Provide at least one uniquely named layer')
    for name, _ in layers:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
            raise ValueError('Layer names may contain only letters, digits, underscore and hyphen')
    if max_member_bytes <= 0:
        raise ValueError('max_member_bytes must be positive')
    layers = [(name, Path(path)) for name, path in layers]
    output = validate_roots([path for _, path in layers], Path(output))
    resources, errors, songs, conversions = [], [], {}, []
    seen_roles = {}

    def register(layer, logical, path, digest, container=None):
        item = {'layer': layer, 'source': logical, 'path': path.as_posix(), 'sha256': digest}
        if container:
            item['container'] = container
        resources.append(item)
        identity = music_role(logical, container_id=(re.fullmatch(r'(\d{8})_msc\.ifs', Path(container).name).group(1)
                              if container and re.fullmatch(r'(\d{8})_msc\.ifs', Path(container).name) else None))
        if not identity:
            return item
        song_id, role = identity
        key = (layer, song_id, role)
        if key in seen_roles:
            if seen_roles[key]['sha256'] != digest:
                errors.append({'layer': layer, 'source': logical,
                               'error': 'Conflicting same-layer song role; no replacement chosen'})
            return item
        seen_roles[key] = item
        song = songs.setdefault(song_id, {'song_id': song_id, 'layers': {}, 'selected_resources': {}})
        song['layers'].setdefault(layer, {})[role] = item
        song['selected_resources'][role] = item
        return item

    for layer, source in layers:
        for file in inventory(source):
            logical = file.relative_to(source).as_posix()
            raw_rel = Path('raw') / layer / safe_relative(logical)
            try:
                digest = preserve_file(file, destination(output, raw_rel))
                register(layer, logical, raw_rel, digest)
                if file.suffix.lower() != '.ifs' or not extract_ifs:
                    continue
                factory = ifs_factory
                if factory is None:
                    try:
                        from ifstools.ifs import IFS
                    except ImportError as exc:
                        raise RuntimeError('IFS export needs optional ifstools; install it or explicitly use --skip-ifs') from exc
                    factory = IFS
                archive = factory(str(file))
                try:
                    base = Path('unpacked') / layer / safe_relative(logical)
                    manifest_text = archive.manifest.to_text()
                    if isinstance(manifest_text, str):
                        manifest_text = manifest_text.encode('utf-8')
                    preserve_bytes(destination(output, base / '_ifs_manifest.xml'), manifest_text)
                    for member in archive.tree.all_files:
                        name = member.full_path.lstrip('/')
                        relative = safe_relative(name)
                        payload = member.load(convert_kbin=False)
                        if len(payload) > max_member_bytes:
                            raise ValueError('IFS member exceeds configured output limit')
                        member_path = base / relative
                        preserve_bytes(destination(output, member_path), payload)
                        register(layer, name, member_path, sha256(payload), container=logical)
                finally:
                    archive.close()
            except Exception as exc:
                errors.append({'layer': layer, 'source': logical, 'error': str(exc)})

    def convert_audio(item, relative):
        from assets.oki4s import bmp_to_wav
        target = destination(output, relative)
        sidecar = target.with_suffix('.audio.json')
        if target.exists():
            if not sidecar.is_file():
                raise ValueError('Existing WAV lacks verified conversion sidecar')
            cached = json.loads(sidecar.read_text(encoding='utf-8'))
            if cached.get('source_sha256') != item['sha256'] or cached.get('wav_sha256') != sha256(target.read_bytes()):
                raise ValueError('Existing WAV/source differs from conversion sidecar')
            audio = cached['audio']
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            audio = bmp_to_wav((output / item['path']).read_bytes(), target)
            write_json(sidecar, {'source_sha256': item['sha256'], 'wav_sha256': sha256(target.read_bytes()), 'audio': audio})
        item['decoded_wav'], item['audio'] = relative.as_posix(), audio

    selected_paths = set()
    for song_id, song in sorted(songs.items()):
        for role, item in song['selected_resources'].items():
            selected_paths.add(item['path'])
            src = output / item['path']
            if role.endswith('.eve'):
                event = chart_events(src.read_bytes())
                rel = Path('songs') / song_id / 'charts' / (role[:-4] + '.events.json')
                write_json(destination(output, rel), event)
                item['events'] = rel.as_posix()
                item['chart_statistics'] = event['statistics']
                if event['unparsed_lines']:
                    errors.append({'song_id': song_id, 'source': item['path'],
                                   'error': 'EVE contains unparsed source lines; raw retained',
                                   'unparsed_lines': event['unparsed_lines']})
            elif decode_audio:
                rel = Path('songs') / song_id / ('bgm.wav' if role == 'bgm.bin' else 'preview.wav')
                try:
                    convert_audio(item, rel)
                except Exception as exc:
                    errors.append({'song_id': song_id, 'source': item['path'], 'error': 'audio: ' + str(exc)})
        song['source_five_assets_present'] = ROLES <= song['selected_resources'].keys()
        song['three_charts_have_notes'] = all(
            any(song['selected_resources'].get(role, {}).get('chart_statistics', {}).get(cmd, 0)
                for cmd in ['PLAY', 'LONG']) for role in ['BSC.eve', 'ADV.eve', 'EXT.eve'])
        write_json(output / 'songs' / song_id / 'info.json', song)

    # Nonselected lower-layer resources and unnamed extras are also retained.
    # Optional audio export includes system sounds, never just ordinary songs.
    for item in resources:
        if item['path'] in selected_paths:
            continue
        src = output / item['path']
        try:
            if src.suffix.lower() == '.eve':
                parsed = chart_events(src.read_bytes())
                relative = Path('extras/charts') / (item['path'] + '.events.json')
                write_json(destination(output, relative), parsed)
                item['events'], item['chart_statistics'] = relative.as_posix(), parsed['statistics']
                if parsed['unparsed_lines']:
                    errors.append({'source': item['path'], 'error': 'Extra EVE contains unparsed lines', 'unparsed_lines': parsed['unparsed_lines']})
            if decode_audio:
                with src.open('rb') as stream:
                    magic = stream.read(4)
                if magic == b'BMP\0':
                    relative = Path('extras/audio') / (item['path'] + '.wav')
                    convert_audio(item, relative)
        except Exception as exc:
            errors.append({'source': item['path'], 'error': 'extra conversion: ' + str(exc)})

    # Keep every non-song file as raw/unpacked. Optional texture conversion does
    # not invent a filename or associate a cover with an unrelated song ID.
    if decode_textures:
        for item in resources:
            src = output / item['path']
            with src.open('rb') as stream:
                magic = stream.read(4)
            if magic != b'PXET':
                continue
            rel = Path('extras/textures') / item['path']
            try:
                from assets.texbin import extract_texbin
                target = destination(output, rel)
                cache = target / 'legacy_conversion.json'
                if target.exists():
                    if not cache.is_file():
                        raise ValueError('Existing texture directory lacks conversion provenance')
                    saved = json.loads(cache.read_text(encoding='utf-8'))
                    if saved.get('source_sha256') != item['sha256']:
                        raise ValueError('Texture source differs from conversion cache')
                    result = saved['result']
                    for record in result.get('records', []):
                        if record.get('status') != 'decoded':
                            continue
                        image = destination(target, record['output'])
                        if not image.is_file() or sha256(image.read_bytes()) != record.get('sha256'):
                            raise ValueError('Cached texture output is absent or changed')
                else:
                    target.mkdir(parents=True)
                    result = extract_texbin(src, target, keep_tdxt=False)
                    write_json(cache, {'source_sha256': item['sha256'], 'result': result})
                conversions.append({'source': item['path'], 'output_directory': rel.as_posix(), 'result': result})
                if result.get('errors'):
                    errors.append({'source': item['path'], 'error': 'Texture decoder reported errors', 'details': result['errors']})
            except Exception as exc:
                errors.append({'source': item['path'], 'error': 'texture: ' + str(exc)})

    complete = sum(s['source_five_assets_present'] for s in songs.values())
    note_complete = sum(s['source_five_assets_present'] and s['three_charts_have_notes'] for s in songs.values())
    stats = {'format': 'legacy_layered_resources', 'source_layers': [name for name, _ in layers],
             'raw_files': sum('container' not in r for r in resources),
             'ifs_member_files': sum('container' in r for r in resources), 'song_ids': len(songs),
             'source_five_asset_ids': complete, 'three_playable_chart_five_asset_ids': note_complete,
             'source_bgm_files': sum('bgm.bin' in s['selected_resources'] for s in songs.values()),
             'source_preview_files': sum('preview.bin' in s['selected_resources'] for s in songs.values()),
             'source_chart_files': sum(role.endswith('.eve') for s in songs.values() for role in s['selected_resources']),
             'decoded_wav_files': sum('decoded_wav' in r for r in resources),
             'extra_event_json_files': sum('events' in r and r['path'] not in selected_paths for r in resources),
             'audio_conversion_requested': decode_audio, 'texture_conversion_requested': decode_textures,
             'ifs_extraction_requested': extract_ifs, 'errors': len(errors),
             'status': 'complete' if not errors else 'complete_with_errors'}
    manifest = {'schema_version': 1, 'stats': stats, 'layer_priority_low_to_high': [name for name, _ in layers],
                'resources': resources, 'songs': list(songs.values()), 'texture_conversions': conversions,
                'notes': ['Raw sources and all named IFS member payloads are preserved.',
                          'Later layers select individual roles; earlier source layers remain intact.',
                          'Source-five-assets and note-bearing chart completeness are separate; neither asserts official menu membership.',
                          'Original preview resources are never replaced by BGM crops.',
                          'Event JSON keeps integer 300 Hz ticks; raw EVE remains authoritative.',
                          'Decoded WAV is source ADPCM reconstruction, not studio-master recovery.',
                          'Unknown files stay in raw/unpacked; no tutorial or version identity is guessed.']}
    write_json(output / 'manifest.json', manifest)
    write_json(output / 'stats.json', stats)
    write_json(output / 'errors.json', errors)
    return manifest
