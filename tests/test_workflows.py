"""Regression fixtures made entirely from temporary synthetic JSON/bytes.

No real IFS, image, song list, archive, or exported game resource is required.
The merger only links fixture bytes; these are intentionally not valid media.
"""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflows._common import configure
from workflows import merge_update

SYNTHETIC_ID = '00000042'
OTHER_SYNTHETIC_ID = '00000043'


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def put_json(path, value):
    put(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def load_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def snapshot(root):
    """Only called on this test's temporary fixture trees."""
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob('*') if p.is_file()}


def dependency_present(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


class OutputBoundaryTests(unittest.TestCase):
    def symlink(self, target, link):
        try:
            link.symlink_to(target, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest('Directory symlinks unavailable: ' + str(exc))

    def test_version_symlink_rejected_before_writing_input(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'input'; source.mkdir()
            put(source / 'original.dat', b'immutable synthetic source')
            out = root / 'output'; out.mkdir()
            self.symlink(source, out / 'v')
            before = snapshot(source)
            argv = ['workflow', '--root', str(out), '--source', str(source), 'v']
            with patch.object(sys, 'argv', argv), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    configure('Synthetic output boundary regression')
            self.assertEqual(caught.exception.code, 2)
            self.assertEqual(snapshot(source), before)
            self.assertFalse((source / 'raw').exists())

    def test_reused_export_with_nested_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'input'; source.mkdir()
            put(source / 'original.dat', b'unchanged')
            out = root / 'output'; (out / 'v').mkdir(parents=True)
            self.symlink(source, out / 'v/raw')
            before = snapshot(source)
            with patch.object(sys, 'argv', ['workflow', '--root', str(out), 'v']), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    configure('Synthetic reused-output boundary regression')
            self.assertEqual(caught.exception.code, 2)
            self.assertEqual(snapshot(source), before)


@unittest.skipUnless(dependency_present('PIL'), 'Optional Pillow dependency unavailable')
class WorkflowExitTests(unittest.TestCase):
    def run_cli(self, module, root):
        # Pass the test interpreter's import paths to its subprocess. This also
        # works with dependencies installed in a caller-selected temporary venv.
        env = dict(os.environ)
        env['PYTHONPATH'] = os.pathsep.join(str(p) for p in sys.path if p)
        return subprocess.run([sys.executable, '-m', module, '--root', str(root), 'v'],
                              cwd=ROOT, env=env, capture_output=True, text=True)

    def test_individual_cli_returns_nonzero_for_recorded_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            put(root / 'v/raw/example.tex', b'unsupported synthetic texture')
            result = self.run_cli('workflows.individual', root)
            report = load_json(root / 'v/individual_texture_stats.json')
            self.assertEqual(len(report['errors']), 1)
            self.assertEqual(report['textures_decoded'], 0)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual((root / 'v/raw/example.tex').read_bytes(), b'unsupported synthetic texture')

    def test_covers_cli_returns_nonzero_for_recorded_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            put_json(root / 'v/songs' / SYNTHETIC_ID / 'cover_sources.json', [
                {'file': 'cover.png', 'texture': 'synthetic.bin', 'name': 'synthetic'}])
            result = self.run_cli('workflows.covers', root)
            report = load_json(root / 'v/cover_provenance_audit.json')
            self.assertEqual(len(report['errors']), 1)
            self.assertEqual(result.returncode, 2, result.stderr)


class IFSMemberPreservationTests(unittest.TestCase):
    def test_manifest_named_original_members_are_not_overwritten(self):
        try:
            ifs_module = importlib.import_module('ifstools.ifs')
        except ImportError as exc:
            self.skipTest('Optional ifstools dependency unavailable: ' + str(exc))
        from assets.ifs import extract_ifs
        payloads = {'ifs_manifest.xml': b'original synthetic XML member',
                    'members.json': b'original synthetic JSON member'}
        loads, closed = [], []

        def factory(_):
            members = []
            for name, data in payloads.items():
                def load(convert_kbin=False, data=data):
                    loads.append(convert_kbin)
                    return data
                members.append(SimpleNamespace(full_path=name, load=load))
            return SimpleNamespace(tree=SimpleNamespace(all_files=members),
                                   manifest=SimpleNamespace(to_text=lambda: '<generated-manifest/>'),
                                   close=lambda: closed.append(True))

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'synthetic.ifs'; out = root / 'out'
            put(source, b'synthetic parser input; never opened by the fake IFS')
            before = source.read_bytes()
            with patch.object(ifs_module, 'IFS', side_effect=factory):
                result = extract_ifs(source, out)
            self.assertEqual(result['errors'], [])
            self.assertEqual(len(result['members']), 2)
            for name, data in payloads.items():
                self.assertEqual((out / 'original' / name).read_bytes(), data)
            self.assertEqual((out / 'ifs_manifest.xml').read_text(), '<generated-manifest/>')
            self.assertEqual(len(load_json(out / 'members.json')['members']), 2)
            self.assertEqual(loads, [False, False])
            self.assertEqual(closed, [True])
            self.assertEqual(source.read_bytes(), before)


class MergeFixture:
    def __init__(self, root):
        self.base, self.update, self.out = root / 'base', root / 'update', root / 'merged'
        # Different supported source layouts exercise path normalization too.
        self.prefix = {self.base: Path('package/contents/data'), self.update: Path('data')}
        for version in [self.base, self.update]:
            (version / 'raw' / self.prefix[version]).mkdir(parents=True)
            (version / 'songs').mkdir()
            put_json(version / 'stats.json', {'finished': True, 'errors': 0})
            put_json(version / 'texture_stats.json', {'errors': [], 'decoded_texture_images': 0})
            put_json(version / 'soundbank_stats.json', {'errors': [], 'decoded_sounds': 0})
            put_json(version / 'individual_texture_stats.json', {'errors': [], 'textures_decoded': 0})
            put_json(version / 'music_metadata.json', {})

    def raw(self, version, relative):
        return version / 'raw' / self.prefix[version] / relative

    def extra(self, version, category, relative):
        return version / 'extras' / category / self.prefix[version] / relative

    def song(self, version, song_id=SYNTHETIC_ID, *, dummy=False, tag='base'):
        folder = version / 'songs' / song_id
        folder.mkdir(parents=True)
        relative = Path('music') / (song_id + '_msc.ifs')
        raw = b'\0' if dummy else (tag + ' synthetic IFS bytes').encode()
        put(self.raw(version, relative), raw)
        row = {'id': song_id, 'layer': 'official',
               'source': (Path('raw') / self.prefix[version] / relative).as_posix(),
               'source_sha256': hashlib.sha256(raw).hexdigest(), 'metadata': {},
               'charts': {}, 'audio': {}, 'members': [], 'errors': [], 'placeholder': dummy}
        if dummy:
            put(folder / 'original/dummy.dat', b'\0')
            row['members'] = [{'name': 'dummy.dat', 'bytes': 1,
                               'sha256': hashlib.sha256(b'\0').hexdigest()}]
        else:
            for code in ['BSC', 'ADV', 'EXT']:
                data = (tag + ' synthetic ' + code + ' EVE').encode()
                put(folder / 'original' / (code.lower() + '.eve'), data)
                put_json(folder / (code + '.events.json'), {'tick_hz': 300, 'events': []})
                put_json(folder / (code + '.mc'), {'note': [{'index': 0}]})
                row['charts'][code] = {'playable': True, 'mc_exported': True}
            for role in ['bgm', 'preview']:
                put(folder / (role + '.wav'), (tag + ' synthetic ' + role).encode())
                row['audio'][role] = {'path': f'songs/{song_id}/{role}.wav'}
        put_json(folder / 'info.json', row)
        metadata = load_json(version / 'music_metadata.json')
        metadata[song_id] = {'music_id': song_id, 'synthetic_tag': tag}
        put_json(version / 'music_metadata.json', metadata)
        return folder

    def texture(self, version, relative, labels):
        relative = Path(relative)
        put(self.raw(version, relative), b'PXET synthetic source ' + version.name.encode())
        dest = self.extra(version, 'textures', relative.with_suffix(''))
        records = []
        for label in labels:
            blob = (version.name + ' synthetic image ' + label).encode()
            put(dest / (label + '.png'), blob)
            records.append({'status': 'decoded', 'name': label, 'output': label + '.png',
                            'width': 2, 'height': 2, 'sha256': hashlib.sha256(blob).hexdigest()})
        put_json(dest / 'texture_manifest.json', {'source': self.raw(version, relative).as_posix(),
                                                 'records': records, 'errors': []})
        return dest

    def bank(self, version, relative, decoded):
        relative = Path(relative)
        put(self.raw(version, relative), b'VA3W synthetic source ' + version.name.encode())
        dest = self.extra(version, 'soundbanks', relative.with_suffix(''))
        records = []
        for index in range(decoded):
            put(dest / f'{index}.wav', f'synthetic sound {index}'.encode())
            records.append({'status': 'decoded', 'output': f'{index}.wav'})
        put_json(dest / 'va3_manifest.json', {'source': self.raw(version, relative).as_posix(),
                                             'count': decoded, 'records': records, 'errors': []})
        return dest

    def individual(self, version, relative):
        relative = Path(relative)
        put(self.raw(version, relative), b'GFGL synthetic standalone image')
        put(self.extra(version, 'individual_textures', relative.with_suffix('.png')), b'synthetic PNG bytes')
        put_json(self.extra(version, 'individual_textures', relative.with_suffix('.json')),
                 {'status': 'decoded', 'source': relative.as_posix()})

    def merge(self):
        before_base, before_update = snapshot(self.base), snapshot(self.update)
        with patch.multiple(merge_update, BASE=self.base, PATCH=self.update, OUT=self.out), redirect_stdout(io.StringIO()):
            merge_update.main()
        return before_base, before_update


class MergeRegressionTests(unittest.TestCase):
    def assert_sources_unchanged(self, fixture, before):
        self.assertEqual(snapshot(fixture.base), before[0], 'base bytes/JSON changed')
        self.assertEqual(snapshot(fixture.update), before[1], 'update bytes/JSON changed')

    def test_dummy_ifs_revokes_old_audio_charts_and_original_members(self):
        with tempfile.TemporaryDirectory() as temp:
            f = MergeFixture(Path(temp))
            f.song(f.base)
            f.song(f.update, dummy=True, tag='update')
            # Missing current metadata is not permission to discard preserved assets.
            put_json(f.update / 'music_metadata.json', {})
            before = f.merge()
            folder = f.out / 'songs' / SYNTHETIC_ID
            for name in ['bgm.wav', 'preview.wav', 'BSC.mc', 'ADV.mc', 'EXT.mc',
                         'BSC.events.json', 'ADV.events.json', 'EXT.events.json', 'original/bsc.eve']:
                self.assertFalse((folder / name).exists(), name)
            self.assertEqual((folder / 'original/dummy.dat').read_bytes(), b'\0')
            self.assertEqual((f.out / 'raw/contents/data/music' / (SYNTHETIC_ID + '_msc.ifs')).read_bytes(), b'\0')
            row = load_json(folder / 'info.json')
            self.assertEqual(row['charts'], {}); self.assertEqual(row['audio'], {})
            self.assertEqual(row['source'], f'raw/contents/data/music/{SYNTHETIC_ID}_msc.ifs')
            self.assertFalse(row['current_catalogued'])
            stats = load_json(f.out / 'stats.json')
            self.assertEqual(stats['song_directories'], 1)
            self.assertEqual(stats['placeholder_containers'], 1)
            for key in ['playable_songs', 'chart_files', 'playable_charts', 'mc_files', 'bgm_decoded', 'preview_decoded']:
                self.assertEqual(stats[key], 0, key)
            self.assert_sources_unchanged(f, before)

    def test_raw_texture_placeholder_revokes_old_png_and_song_cover(self):
        with tempfile.TemporaryDirectory() as temp:
            f = MergeFixture(Path(temp)); folder = f.song(f.base)
            image_dir = f.texture(f.base, 'graphics/art.bin', ['synthetic_cover'])
            image = image_dir / 'synthetic_cover.png'
            put(folder / 'cover.png', image.read_bytes())
            put_json(folder / 'cover_sources.json', [{
                'texture': (f.prefix[f.base] / 'graphics/art.bin').as_posix(),
                'name': 'synthetic_cover', 'file': 'cover.png',
                'candidate_png': image.relative_to(f.base).as_posix(),
                'candidate_width': 2, 'candidate_height': 2}])
            put(f.raw(f.update, 'graphics/art.bin'), b'\0')
            before = f.merge()
            self.assertFalse((f.out / 'extras/textures/contents/data/graphics/art').exists())
            self.assertFalse((f.out / 'songs' / SYNTHETIC_ID / 'cover.png').exists())
            self.assertEqual(load_json(f.out / 'songs' / SYNTHETIC_ID / 'cover_sources.json'), [])
            replaced = load_json(f.out / 'songs' / SYNTHETIC_ID / 'cover_sources.replaced_by_update.json')
            self.assertEqual(len(replaced), 1)
            self.assertEqual((f.out / 'raw/contents/data/graphics/art.bin').read_bytes(), b'\0')
            self.assertEqual(load_json(f.out / 'texture_stats.json')['decoded_texture_images'], 0)
            self.assert_sources_unchanged(f, before)

    def test_multidot_tex_placeholder_removes_exact_derivatives_only(self):
        with tempfile.TemporaryDirectory() as temp:
            f = MergeFixture(Path(temp)); f.song(f.base)
            f.individual(f.base, 'emblem/synthetic.v1.tex')
            f.individual(f.base, 'emblem/synthetic.tex')
            put(f.raw(f.update, 'emblem/synthetic.v1.tex'), b'\0')
            before = f.merge()
            output = f.out / 'extras/individual_textures/contents/data/emblem'
            self.assertFalse((output / 'synthetic.v1.png').exists())
            self.assertFalse((output / 'synthetic.v1.json').exists())
            self.assertEqual((output / 'synthetic.png').read_bytes(), b'synthetic PNG bytes')
            self.assertTrue((output / 'synthetic.json').is_file())
            self.assertEqual(load_json(f.out / 'individual_texture_stats.json')['textures_decoded'], 1)
            self.assert_sources_unchanged(f, before)

    def test_overlay_totals_read_merged_manifests_and_do_not_double_count(self):
        with tempfile.TemporaryDirectory() as temp:
            f = MergeFixture(Path(temp))
            f.song(f.base, SYNTHETIC_ID, tag='old')
            f.song(f.base, OTHER_SYNTHETIC_ID, tag='unchanged')
            f.song(f.update, SYNTHETIC_ID, tag='new')
            f.texture(f.base, 'graphics/shared.bin', ['old_a', 'old_b'])
            f.texture(f.base, 'graphics/unchanged.bin', ['keep'])
            f.texture(f.update, 'graphics/shared.bin', ['replacement'])
            f.texture(f.update, 'graphics/new.bin', ['new_a', 'new_b'])
            f.bank(f.base, 'audio/shared.va3', 1)
            f.bank(f.base, 'audio/unchanged.va3', 1)
            f.bank(f.update, 'audio/shared.va3', 2)
            # Source totals overlap: textures 3+3, sounds 2+2. The merged totals
            # must instead be 1+1+2 textures and 2+1 sound records.
            put_json(f.base / 'texture_stats.json', {'decoded_texture_images': 3, 'errors': []})
            put_json(f.update / 'texture_stats.json', {'decoded_texture_images': 3, 'errors': []})
            put_json(f.base / 'soundbank_stats.json', {'decoded_sounds': 2, 'errors': []})
            put_json(f.update / 'soundbank_stats.json', {'decoded_sounds': 2, 'errors': []})
            before = f.merge()
            stats = load_json(f.out / 'stats.json')
            self.assertEqual(stats['song_directories'], 2)
            self.assertEqual(stats['changed_song_containers'], 1)
            self.assertEqual(stats['chart_files'], 6)
            self.assertEqual(stats['bgm_decoded'], 2)
            self.assertEqual(stats['preview_decoded'], 2)
            self.assertEqual(stats['metadata_entries'], 1)
            self.assertEqual(stats['preserved_metadata_entries'], 2)
            textures = load_json(f.out / 'texture_stats.json')
            banks = load_json(f.out / 'soundbank_stats.json')
            self.assertEqual(textures['texbin_containers'], 3)
            self.assertEqual(textures['decoded_texture_images'], 4)
            self.assertEqual(len(banks['banks']), 2)
            self.assertEqual(banks['decoded_sounds'], 3)
            self.assertEqual(textures['errors'], []); self.assertEqual(banks['errors'], [])
            replaced = f.out / 'extras/textures/contents/data/graphics/shared'
            self.assertFalse((replaced / 'old_a.png').exists())
            self.assertFalse((replaced / 'old_b.png').exists())
            self.assertTrue((replaced / 'replacement.png').is_file())
            self.assertEqual((f.out / 'songs' / SYNTHETIC_ID / 'bgm.wav').read_bytes(), b'new synthetic bgm')
            self.assertEqual((f.out / 'songs' / OTHER_SYNTHETIC_ID / 'bgm.wav').read_bytes(), b'unchanged synthetic bgm')
            rows = {row['id']: row for row in load_json(f.out / 'manifest.json')}
            self.assertFalse(rows[OTHER_SYNTHETIC_ID]['current_catalogued'])
            self.assertEqual(rows[SYNTHETIC_ID]['merge_source_version'], 'update')
            self.assertEqual(rows[OTHER_SYNTHETIC_ID]['merge_source_version'], 'base')
            self.assert_sources_unchanged(f, before)


if __name__ == '__main__':
    unittest.main()
