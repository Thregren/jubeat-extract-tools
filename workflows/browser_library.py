#!/usr/bin/env python3
"""把已归一化的歌曲库导出为无 MCZ 的目录曲库，并按版本生成 ZIP64 包。"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import sys
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

FORMAT = 'jubeat-directory-v1'
DIFFS = ('BSC', 'ADV', 'EXT')


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def relative(value):
    value = str(value)
    p = PurePosixPath(value)
    if any(ord(c) < 32 for c in value) or not value or p.is_absolute() or '\\' in value or ':' in value or any(s in ('', '.', '..') for s in value.split('/')):
        raise ValueError('不安全的相对路径: ' + value)
    return unicodedata.normalize('NFC', value)


def inside(root, value):
    p = root / relative(value)
    if root.resolve() not in p.resolve().parents:
        raise ValueError('路径越出曲库: ' + str(value))
    return p


def link_file(source, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if os.path.samefile(source, dest):
            return
        if source.stat().st_size == dest.stat().st_size and digest(source) == digest(dest):
            return
        raise ValueError('拒绝覆盖不同内容的文件: ' + str(dest))
    try:
        os.link(source, dest)
    except OSError:
        temp = dest.with_name(dest.name + '.tmp')
        shutil.copyfile(source, temp)
        if digest(source) != digest(temp):
            raise ValueError('跨文件系统复制校验失败: ' + str(source))
        temp.replace(dest)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def export_library(source, output, limit=None):
    source = Path(source).resolve()
    output = Path(output).resolve()
    if source == output or source in output.parents:
        raise ValueError('输出必须位于统一库以外，避免修改或递归导出源库')
    plan = load(source / '_reports/生成计划.json')
    result = load(source / '_reports/生成结果.json')
    index = load(source / '_reports/歌曲索引.json')
    if result.get('status') != 'complete' or len(index) != len(plan['packages']):
        raise ValueError('只接受完整且计划/索引对应的统一库')
    if (output / 'data/library.json').exists():
        previous = load(output / 'data/library.json')
        if previous.get('format') != FORMAT:
            raise ValueError('输出目录已经属于另一种曲库格式')
    by_version = {}
    for p in source.glob('*/来源索引.json'):
        version = relative(p.parent.name)
        by_version[version] = []
        save(output / 'data/sources' / (version + '.json'), load(p))
    entries = []
    pairs = list(zip(plan['packages'], index))
    if limit is not None:
        pairs = pairs[:limit]
    for number, (package, song) in enumerate(pairs, 1):
        if package['identity'] != song['identity'] or package['source'] != song['source']:
            raise ValueError('计划与歌曲索引不对应')
        original = inside(source, package['path'])
        key = relative(package['path'])
        folder = 'songs/' + key
        target = inside(output, folder)
        assets = {}
        charts = []
        missing = list(song.get('missing', []))
        for kind in ('bgm', 'preview'):
            if kind in song.get('audio', {}):
                path = original / (kind + '.wav')
                if path.stat().st_size != song['audio'][kind]['bytes']:
                    raise ValueError('WAV 长度不符: ' + str(path))
                link_file(path, target / path.name)
                assets['audio' if kind == 'bgm' else 'preview'] = folder + '/' + path.name
        if song.get('cover'):
            path = original / 'cover.png'
            if digest(path) != song['cover']['sha256']:
                raise ValueError('封面哈希不符: ' + str(path))
            link_file(path, target / path.name)
            assets['cover'] = folder + '/cover.png'
            # 懒加载列表使用原封面，不伪造缺失图片；构建器可单独派生缩略图。
            assets['thumb'] = assets['cover']
        for code in DIFFS:
            if code not in song.get('charts', {}):
                continue
            info = song['charts'][code]
            mc = original / 'charts' / (code + '.mc')
            data = load(mc)
            if not isinstance(data.get('note'), list) or not isinstance(data.get('time'), list):
                raise ValueError('兼容谱面 JSON 结构不符: ' + str(mc))
            dest = target / 'charts' / (code + '.json')
            link_file(mc, dest)
            assets['chart:' + code] = folder + '/charts/' + dest.name
            exact = original / 'charts' / (code + '.events.json')
            link_file(exact, target / 'charts' / exact.name)
            assets['exact:' + code] = folder + '/charts/' + exact.name
            for raw in (original / 'original').glob(code + '.*'):
                link_file(raw, target / 'original' / raw.name)
            level = info.get('level')
            holds = info['holds']
            charts.append(dict(code=code, level='' if level is None else str(level),
                               levelKnown=level not in (None, ''), notes=info['notes'] + holds,
                               heads=info['notes'], holds=holds, notesKnown=True,
                               playable=info['playable']))
        for raw in (original / 'original').rglob('*'):
            if raw.is_file():
                rel = raw.relative_to(original / 'original').as_posix()
                inside(original / 'original', rel)
                link_file(raw, target / 'original' / rel)
        title = package.get('title') or 'ID ' + package['source']['id']
        version = key.split('/')[0]
        entry = dict(id=key, identity=package['identity'], title=title,
                     titleKnown=bool(package.get('title')), artist=package.get('artist') or '',
                     version=version, source=package['source'], category=package['category'],
                     cover='cover.png' if song.get('cover') else '', assets=assets,
                     charts=charts, missing=missing,
                     resourceOnly=not any(c['playable'] for c in charts),
                     originalSource=key)
        if package.get('metadataEvidence'):
            entry['metadataEvidence'] = package['metadataEvidence']
        save(target / 'song.json', dict(format=FORMAT, **entry))
        link_file(original / 'provenance.json', target / 'provenance.json')
        by_version.setdefault(version, []).append(entry)
        entries.append(entry)
        if number % 100 == 0:
            print('导出歌曲目录', number, '/', len(pairs), flush=True)
    catalogs = []
    for version, songs in sorted(by_version.items()):
        rel = 'data/versions/' + version + '.json'
        save(output / rel, dict(format=FORMAT, version=1, versions=[version], songs=songs))
        catalogs.append(rel)
    # 分版本包可独立安装；尚未安装的版本索引返回404时由加载器跳过。
    save(output / 'data/library.json', dict(format=FORMAT, version=1, versions=[], songs=[],
                                          versionIndexes=catalogs))
    save(output / 'data/export.json', dict(format=FORMAT, status='sample' if limit else 'complete',
         packages=len(entries), playablePackages=sum(not e['resourceOnly'] for e in entries),
         resourceOnly=sum(e['resourceOnly'] for e in entries),
         charts=sum(len(e['charts']) for e in entries),
         audio=sum('audio' in e['assets'] for e in entries),
         previews=sum('preview' in e['assets'] for e in entries),
         covers=sum('cover' in e['assets'] for e in entries),
         versions={v: len(s) for v, s in by_version.items()},
         policy='PCM24 WAV和PNG不转码；精确事件保留源tick_hz/原谱/来源证明；兼容JSON沿用现有播放器拍点结构'))
    return by_version


class HashingZipStream:
    """Non-seekable ZIP64 stream: hash final bytes while writing data descriptors."""
    def __init__(self, stream):
        self.stream = stream
        self.hash = hashlib.sha256()
    def write(self, data):
        size = self.stream.write(data)
        if size != len(data):
            raise OSError('ZIP写入不足')
        self.hash.update(data)
        return size
    def tell(self):
        return self.stream.tell()
    def flush(self):
        self.stream.flush()


def pack_versions(root, archive_dir, verify=False, compress_audio=False, workers=1):
    root = Path(root).resolve()
    archive_dir = Path(archive_dir).resolve()
    if archive_dir == root or root in archive_dir.parents:
        raise ValueError('版本包目录必须位于浏览器曲库以外')
    archive_dir.mkdir(parents=True, exist_ok=True)
    report = archive_dir / '打包校验.json'
    rows = []
    for catalog in sorted((root / 'data/versions').glob('*.json')):
        version = relative(catalog.stem)
        songs = load(catalog)['songs']
        files = {catalog.relative_to(root).as_posix()}
        source_index = root / 'data/sources' / (version + '.json')
        if source_index.exists():
            files.add(source_index.relative_to(root).as_posix())
        for song in songs:
            folder = inside(root, 'songs/' + relative(song['id']))
            for path in folder.rglob('*'):
                if path.is_file():
                    name = path.relative_to(root).as_posix()
                    inside(root, name)
                    files.add(name)
        rows.append((version, files, len(songs)))

    def one(item):
        version, files, songs = item
        dest = archive_dir / (version + '.zip')
        fingerprint = hashlib.sha256(json.dumps(dict(compressAudio=compress_audio,
            files=[(f, (root/f).stat().st_size, (root/f).stat().st_mtime_ns)
                   for f in sorted(files)]), ensure_ascii=False).encode()).hexdigest()
        stamp = dest.with_suffix('.zip.json')
        if dest.exists() and stamp.exists():
            old = load(stamp)
            st = dest.stat()
            if old.get('fingerprint') == fingerprint and old.get('zip_bytes') == st.st_size and old.get('mtime_ns') == st.st_mtime_ns and (not verify or old.get('crc_verified')):
                print('复用版本包', version, flush=True)
                return old
        temp = dest.with_name(dest.name + '.partial')
        manifest = []
        with temp.open('wb') as raw_zip:
            output = HashingZipStream(raw_zip)
            with zipfile.ZipFile(output, 'w', allowZip64=True) as archive:
                for name in sorted(files):
                    source = inside(root, name)
                    before = source.stat()
                    kind = zipfile.ZIP_DEFLATED if source.suffix == '.json' or compress_audio else zipfile.ZIP_STORED
                    info = zipfile.ZipInfo.from_file(source, name)
                    info.compress_type = kind
                    h = hashlib.sha256()
                    size = 0
                    with source.open('rb') as src, archive.open(info, 'w', force_zip64=True) as out:
                        for chunk in iter(lambda: src.read(1024 * 1024), b''):
                            out.write(chunk); h.update(chunk); size += len(chunk)
                    after = source.stat()
                    if size != before.st_size or (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise RuntimeError('打包时源文件发生变化: ' + name)
                    manifest.append(dict(path=name, bytes=size, sha256=h.hexdigest()))
                archive.writestr('PACKAGE.json', json.dumps(dict(format=FORMAT, version=version,
                                 songs=songs, files=manifest), ensure_ascii=False).encode(), compress_type=zipfile.ZIP_DEFLATED)
        with zipfile.ZipFile(temp) as archive:
            if len(archive.infolist()) != len(files) + 1:
                raise RuntimeError('ZIP成员数错误')
            if verify:
                bad = archive.testzip()
                if bad:
                    raise RuntimeError('ZIP CRC校验失败: ' + bad)
        temp.replace(dest)
        st = dest.stat()
        row = dict(version=version, packages=songs, files=len(files), zip_bytes=st.st_size,
                   mtime_ns=st.st_mtime_ns, fingerprint=fingerprint, crc_verified=verify,
                   zip_sha256=output.hash.hexdigest())
        save(stamp, row)
        print('版本包完成', version, st.st_size, 'bytes', flush=True)
        return row
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        packed = list(pool.map(one, rows))
    save(report, dict(status='complete', format=FORMAT, archives=packed))
    return packed


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--archives', type=Path)
    p.add_argument('--verify-zips', action='store_true')
    p.add_argument('--compress-audio', action='store_true')
    p.add_argument('--workers', type=int, default=1)
    p.add_argument('--limit', type=int)
    args = p.parse_args(argv)
    try:
        export_library(args.library, args.output, args.limit)
        if args.archives:
            pack_versions(args.output, args.archives, args.verify_zips, args.compress_audio, max(1, min(args.workers, 4)))
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print('导出失败: ' + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
