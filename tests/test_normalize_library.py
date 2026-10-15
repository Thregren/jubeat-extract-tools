"""Synthetic metadata, EVE, PCM and PNG only; no game data fixtures."""
from pathlib import Path
import hashlib
import importlib.util
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis.compare_versions import Reader
from analysis.identity_relations import resolve_identities
from workflows.normalize_library import (LibraryBuilder, POLICY_VERSION, contained,
    compressed_audio_parameters, itunes_gapless_samples, file_sha256, fingerprint,
    read_json, wav_info, write_json)

HAS_PIL = importlib.util.find_spec("PIL") is not None
HAS_FFMPEG = shutil.which("ffmpeg") is not None
HAS_FFPROBE = shutil.which("ffprobe") is not None


def make_wav(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        output.writeframes(struct.pack("<480h", *[i % 200 - 100 for i in range(480)]))


def fixture(root, version_specs):
    versions = []
    for number, specs in version_specs:
        folder = root / f"{number:02d}_synthetic"
        rows = []
        for spec in specs:
            sid = spec.get("id", "10000001")
            song = folder / "songs" / sid
            row = dict(song_id=sid, selected_resources={}, layers={"base": {}}, metadata={}, covers=[])
            for index, code in enumerate(("BSC", "ADV", "EXT")):
                if spec.get("charts", True):
                    raw = f"0,TEMPO,500000\n0,HAKU,0\n30,PLAY,{(spec.get('panel', 0) + index) % 16}\n300,END,0\n".encode()
                    path = song / "base" / (code + ".eve")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
                    relative = str(path.relative_to(folder))
                    row["selected_resources"][code + ".eve"] = dict(layer="base", path=relative, sha256=file_sha256(path))
                    row["layers"]["base"][code + ".eve"] = relative
            for kind in ("bgm", "preview"):
                if kind == "preview" and not spec.get("preview", True):
                    continue
                path = song / (kind + ".wav")
                make_wav(path)
                row["selected_resources"][kind + ".bmpaudio.bin"] = dict(
                    decoded_wav=str(path.relative_to(folder)), sha256=hashlib.sha256(kind.encode()).hexdigest(),
                    audio=dict(frames=480, sample_rate=24000, channels=1))
            if spec.get("cover"):
                from PIL import Image
                path = song / "cover.png"
                Image.new("RGB", (2, 2), (8, 16, 32)).save(path)
                row["covers"].append(dict(path=str(path.relative_to(folder)), width=2, height=2))
            rows.append(row)
        write_json(folder / "manifest.json", dict(songs=rows))
        versions.append(Reader(root, {}).load(folder))
    path = root / "comparison.json"
    write_json(path, dict(completion="complete", sample=False, issues=[], versions=versions))
    return path


def compressed_fixture(root, codec):
    fixture(root, [(1, [dict()])])
    number = 10 if codec == "flac" else 16
    folder = root / f"{number:02d}_synthetic"
    (root / "01_synthetic").rename(folder)
    song = folder / "songs/10000001"
    row = dict(id="10000001", layer="official" if number == 10 else "mobile",
               platform="arcade" if number == 10 else "mobile", charts={}, members=[], audio={})
    for code in ("BSC", "ADV", "EXT"):
        raw = (song / "base" / (code + ".eve")).read_bytes()
        if codec == "aac":
            records = []
            for line in raw.decode().splitlines():
                tick, command, value = line.split(",")
                records.append(struct.pack("<II", (int(tick) << 8) | {"TEMPO": 5, "HAKU": 4, "PLAY": 1, "END": 2}[command], int(value)))
            header = bytearray(96)
            header[:4] = b"TEST"
            struct.pack_into("<I", header, 4, len(records))
            raw = bytes(header) + b"".join(records)
        path = song / ("charts/" + code + ".seq" if codec == "aac" else "original/" + code + ".eve")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        row["charts"][code] = dict(raw=str(path.relative_to(folder)), sha256=file_sha256(path), playable=True)
    for kind in ("bgm", "preview"):
        path = song / (kind + (".m4a" if codec == "aac" else ".flac"))
        subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-i", str(song / (kind + ".wav")),
                        "-c:a", codec, str(path)], check=True, capture_output=True)
        row["audio"]["idx" if number == 10 and kind == "preview" else kind] = dict(
            path=str(path.relative_to(folder)), raw_sha256=file_sha256(path), sample_rate=24000, channels=1)
    write_json(folder / "manifest.json", [row])
    report = root / "comparison.json"
    write_json(report, dict(completion="complete", sample=False, issues=[], versions=[Reader(root, {}).load(folder)]))
    return report


class PlanTests(unittest.TestCase):
    def test_itunes_smpb_exact_samples_and_priming_fallback(self):
        for valid, padding in ((5129170, 1006), (441000, 280)):
            priming = 2112
            total = valid + priming + padding
            probe = dict(streams=[dict(codec_name="aac", profile="LC", sample_rate="44100", channels=2,
                         initial_padding=priming, duration_ts=total, time_base="1/44100", nb_samples=total)],
                         packets=[dict(side_data_list=[dict(skip_samples=priming, discard_padding=0)])],
                         format=dict(tags={"iTunSMPB": f" 00000000 {priming:08X} {padding:08X} {valid:016X} 00000000"}))
            parameters = compressed_audio_parameters(probe)
            self.assertEqual((parameters["frames"], parameters["frame_tolerance"]), (valid, 0))
            self.assertEqual(parameters["itunes_gapless"]["container_frames_minus_valid"], priming + padding)
            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                builder = LibraryBuilder(base / "exports", base / "library", base / "comparison.json")
                info = dict(format_tag=1, bits_per_sample=24, channels=2, sample_rate=44100, frames=valid)
                with patch.object(builder, "source_audio_parameters", return_value=parameters):
                    builder.validate_audio({}, info)
                    with self.assertRaisesRegex(ValueError, "frame count"):
                        builder.validate_audio({}, dict(info, frames=valid + 1))
                probe["format"]["tags"] = {}
                del probe["streams"][0]["nb_samples"]
                fallback = compressed_audio_parameters(probe)
                self.assertEqual(fallback["frame_tolerance"], priming + 1024)
                with patch.object(builder, "source_audio_parameters", return_value=fallback):
                    builder.validate_audio({}, info)
                    with self.assertRaisesRegex(ValueError, "frame count"):
                        builder.validate_audio({}, dict(info, frames=valid - 2048))

    def test_malformed_or_zero_smpb_is_not_a_valid_count(self):
        for value in (None, "garbage", "00000000 00000840 00000000 0000000000000000",
                      "00000000 00000840 000003EE NOT-HEX"):
            self.assertIsNone(itunes_gapless_samples(value))

    def test_video_is_index_only_not_asserted_dummy(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "exports"
            comparison = fixture(root, [(1, [dict(charts=False)])])
            folder = root / "01_synthetic"
            write_json(folder / "manifest.json", dict(songs=[dict(song_id="99999999",
                       selected_resources={}, assets=[dict(path="synthetic-video.wmv")])]))
            write_json(comparison, dict(completion="complete", sample=False, issues=[], versions=[Reader(root, {}).load(folder)]))
            plan = LibraryBuilder(root, base / "library", comparison).build_plan()
            self.assertEqual(plan["summary"]["index_only_observations"], 1)
            self.assertIn("non-song video", plan["excluded_index_only"][0]["reason"])
            self.assertFalse(plan["excluded_index_only"][0]["observed_placeholder"])

    def test_missing_charts_and_namespace_only_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "exports"
            comparison = fixture(root, [(1, [dict(charts=False, preview=False)])])
            plan = LibraryBuilder(root, base / "library", comparison).build_plan()
            package = plan["packages"][0]
            self.assertEqual(package["missing_charts"], ["BSC", "ADV", "EXT"])
            self.assertTrue({"chart:BSC", "chart:ADV", "chart:EXT", "preview", "cover"} <= set(package["missing"]))
            self.assertEqual(plan["policy"]["identity"], "namespace_id_only")

    def test_same_version_multi_bundle_paths_and_stale_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "exports"
            comparison = fixture(root, [(1, [dict()]),
                (2, [dict(panel=1), dict(id="10000002", panel=2)]),
                (3, [dict(), dict(id="10000002")])])
            audit = base / "audio-audit.json"
            write_json(audit, dict(arcade_alias_group_audio_evidence=[dict(
                arcade_ids=["10000001", "10000002"], all_ids_connected_by_identical_dual_audio=True)]))
            builder = LibraryBuilder(root, base / "library", comparison, alias_audio_audit=audit)
            plan = builder.build_plan()
            changes = [p for p in plan["packages"] if p["category"] == "谱面变化"]
            self.assertEqual(len(changes), 2)
            self.assertEqual(len({p["path"] for p in changes}), 2)
            self.assertTrue(all("__" in p["path"] for p in changes))
            manifest = root / "01_synthetic/manifest.json"
            manifest.write_text(manifest.read_text() + " ")
            with self.assertRaisesRegex(ValueError, "stale"):
                builder.build_plan()

    def test_paths_and_unowned_output_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "exports"
            comparison = fixture(root, [(1, [dict()])])
            with self.assertRaises(ValueError):
                contained(root.resolve(), "../outside")
            outside = base / "outside"
            outside.mkdir()
            (root / "escape").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                contained(root.resolve(), "escape/file.json")
            output = base / "library"
            output.mkdir()
            (output / "unrelated.txt").write_text("keep")
            with self.assertRaisesRegex(ValueError, "nonempty"):
                LibraryBuilder(root, output, comparison).build_plan()
            report = read_json(comparison)
            report["versions"][0]["directory"] = "../outside"
            write_json(comparison, report)
            with self.assertRaises(ValueError):
                LibraryBuilder(root, base / "another", comparison).build_plan()
            owned = base / "owned"
            owned.mkdir()
            (owned / "charts").symlink_to(outside, target_is_directory=True)
            builder = LibraryBuilder(root, owned, comparison)
            with self.assertRaises(ValueError):
                builder.write_output("charts/escaped.json", {})
            self.assertFalse((outside / "escaped.json").exists())

    def test_identity_requires_current_evidence(self):
        def observation(key, platform):
            return dict(key=key, platform=platform, charts={d: dict(playable=True, gameplay_sha256=d)
                        for d in ("BSC", "ADV", "EXT")}, audio={})
        observations = [observation("arcade:10000001", "arcade"), observation("16_app:100000101", "mobile")]
        union, _ = resolve_identities(observations)
        self.assertNotEqual(union.get(observations[0]["key"]), union.get(observations[1]["key"]))
        relations = dict(unique_three_chart_relations=[dict(arcade_id="10000001", mobile_version="16_app", mobile_id="100000101")])
        union, audit = resolve_identities(observations, relations)
        self.assertEqual(len(audit["accepted"]), 1)
        mod = observation("arcade:10000002", "arcade")
        mod.update(layer="mod_omnimix", kind="release_package")
        _, audit = resolve_identities(observations + [mod], relations)
        self.assertEqual(len(audit["accepted"]), 1)
        official = observation("arcade:10000003", "arcade")
        official.update(layer="official", kind="release_package")
        _, audit = resolve_identities(observations + [official], relations)
        self.assertEqual(len(audit["rejected"]), 1)
        observations[1]["charts"]["EXT"]["gameplay_sha256"] = "different"
        _, audit = resolve_identities(observations, relations)
        self.assertEqual(len(audit["rejected"]), 1)

    @unittest.skipUnless(HAS_PIL, "Pillow required for synthetic PNG relation test")
    def test_explicit_cover_reading_alias_with_different_audio(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temporary:
            left, right = Path(temporary) / "left.png", Path(temporary) / "right.png"
            Image.new("RGB", (2, 2), (17, 31, 63)).save(left)
            shutil.copyfile(left, right)
            observations = []
            for sid, cover, audio in [("10000001", left, "different-left"), ("10000002", right, "different-right")]:
                observations.append(dict(key="arcade:" + sid, platform="arcade", cover=str(cover),
                    metadata=dict(name_reading_cp932="SYNTHETIC READING"),
                    charts={d: dict(playable=True, gameplay_sha256=d) for d in ("BSC", "ADV", "EXT")},
                    audio={k: dict(source_sha256=audio + k) for k in ("bgm", "preview")}))
            group = dict(arcade_ids=["10000001", "10000002"], name_reading_cp932="SYNTHETIC READING",
                         cover_sha256=file_sha256(left), evidence="manually inspected synthetic cover")
            hasher = lambda observation: file_sha256(observation["cover"])
            union, _ = resolve_identities(observations, cover_hash=hasher)
            self.assertNotEqual(union.get("arcade:10000001"), union.get("arcade:10000002"))
            relations = dict(arcade_cover_reading_alias_groups=[group])
            union, audit = resolve_identities(observations, relations, cover_hash=hasher)
            self.assertEqual(len(audit["accepted"]), 1)
            self.assertEqual(union.get("arcade:10000001"), union.get("arcade:10000002"))
            Image.new("RGB", (2, 2), (99, 31, 63)).save(right)
            union, audit = resolve_identities(observations, relations, cover_hash=hasher)
            self.assertEqual(len(audit["rejected"]), 1)
            self.assertNotEqual(union.get("arcade:10000001"), union.get("arcade:10000002"))

    @unittest.skipUnless(HAS_PIL, "Pillow required for synthetic visual-title certificate test")
    def test_different_covers_need_bound_visual_title_and_bpm_levels(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temporary:
            observations, certificates, cover_hashes = [], [], {}
            for index, sid in enumerate(("10000001", "10000002")):
                path = Path(temporary) / (sid + ".png")
                Image.new("RGB", (2, 2), (index * 99, 16, 32)).save(path)
                cover_hashes[sid] = file_sha256(path)
                layer = "mod_omnimix" if index == 0 else "official"
                observations.append(dict(key="arcade:" + sid, id=sid, version="11_synthetic", layer=layer,
                    platform="arcade", cover=str(path), source_manifest="11_synthetic/manifest.json",
                    metadata=dict(name_reading_cp932="SYNTHETIC READING", bpm_min="-1", bpm_max="154",
                                  level_bsc="3", level_adv="7", detail_level_ext="9.0"),
                    charts={d: dict(playable=True, gameplay_sha256=d) for d in ("BSC", "ADV", "EXT")},
                    audio={k: dict(source_sha256=sid + k) for k in ("bgm", "preview")}))
                certificates.append(dict(id=sid, version="11_synthetic", layer=layer,
                    manifest="11_synthetic/manifest.json", cover_sha256=cover_hashes[sid]))
            group = dict(arcade_ids=list(cover_hashes), name_reading_cp932="SYNTHETIC READING",
                         title="SYNTHETIC VISUALLY CHECKED TITLE", title_visual_verified=True,
                         cover_sha256_by_id=cover_hashes, observations=certificates)
            calls = []
            def hasher(observation):
                calls.append(observation["id"])
                return file_sha256(observation["cover"])
            relations = dict(arcade_cover_reading_alias_groups=[group])
            _, audit = resolve_identities(observations, relations, cover_hash=hasher)
            self.assertEqual(len(audit["accepted"]), 1)
            self.assertEqual(calls, ["10000001", "10000002"])
            group["title_visual_verified"] = False
            _, audit = resolve_identities(observations, relations, cover_hash=hasher)
            self.assertEqual(len(audit["rejected"]), 1)
            group["title_visual_verified"] = True
            observations[1]["metadata"]["bpm_max"] = "155"
            _, audit = resolve_identities(observations, relations, cover_hash=hasher)
            self.assertEqual(len(audit["rejected"]), 1)
            observations[1]["metadata"]["bpm_max"] = "154"
            certificates[0]["layer"] = "official"
            _, audit = resolve_identities(observations, relations, cover_hash=hasher)
            self.assertEqual(len(audit["rejected"]), 1)


@unittest.skipUnless(HAS_PIL and HAS_FFMPEG, "Pillow and ffmpeg required for end-to-end media test")
class ExportTests(unittest.TestCase):
    @unittest.skipUnless(HAS_FFPROBE, "ffprobe required for compressed-source validation")
    def test_actual_aac_and_flac_end_to_end_and_resume(self):
        for codec in ("aac", "flac"):
            with self.subTest(codec=codec), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                comparison = compressed_fixture(base / "exports", codec)
                builder = LibraryBuilder(base / "exports", base / "library", comparison)
                plan = builder.build_plan()
                self.assertEqual(builder.run(plan), 0)
                folder = builder.output / plan["packages"][0]["path"]
                output = wav_info(folder / "bgm.wav")
                self.assertEqual((output["bits_per_sample"], output["sample_rate"], output["channels"]), (24, 24000, 1))
                self.assertEqual(read_json(folder / "song.json")["audio"]["bgm"]["source_parameters"]["codec_name"], codec)
                if codec == "flac":
                    self.assertEqual(output["frames"], 480)
                else:
                    self.assertLessEqual(abs(output["frames"] - 480), 1024)
                self.assertEqual(builder.run(plan), 0)

    def test_pcm24_guides_cover_provenance_mc_and_validated_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "exports"
            comparison = fixture(root, [(1, [dict()]), (2, [dict(cover=True)])])
            builder = LibraryBuilder(root, base / "library", comparison)
            plan = builder.build_plan()
            self.assertEqual(len(plan["packages"]), 1)
            package = plan["packages"][0]
            self.assertTrue(package["asset_provenance"]["cover"]["borrowed"])
            self.assertEqual(builder.run(plan), 0)
            folder = builder.output / package["path"]
            self.assertEqual(wav_info(folder / "bgm.wav")["bits_per_sample"], 24)
            self.assertEqual(wav_info(folder / "bgm.wav")["sample_rate"], 24000)
            self.assertEqual(wav_info(folder / "bgm.wav")["channels"], 1)
            self.assertEqual(wav_info(folder / "bgm.wav")["frames"], 480)
            self.assertEqual(read_json(folder / "charts/BSC.mc")["meta"]["song"]["title"], "ID 10000001")
            self.assertIsNone(read_json(folder / "song.json")["title"])
            self.assertEqual(builder.run(builder.build_plan()), 0)
            (folder / "charts/BSC.mc").write_text("corrupted")
            self.assertEqual(builder.run(plan), 0)
            self.assertIn("note", read_json(folder / "charts/BSC.mc"))
            chart = package["charts"]["BSC"]
            source = root / package["source"]["version"] / chart["raw_eve"]
            source.write_bytes(source.read_bytes() + b"0,PLAY,9\n")
            self.assertNotEqual(builder.run(plan), 0)

    def test_existing_pcm16_cache_cannot_pass_a_sidecar(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "exports"
            comparison = fixture(root, [(1, [dict()])])
            builder = LibraryBuilder(root, base / "library", comparison)
            package = builder.build_plan()["packages"][0]
            asset = package["audio"]["bgm"]
            source = root / asset["path"]
            key = fingerprint(dict(source_audio_sha256=file_sha256(source), policy=POLICY_VERSION))
            cache = builder.output / f"_cache/audio/{key}.wav"
            cache.parent.mkdir(parents=True)
            shutil.copyfile(source, cache)
            write_json(cache.with_suffix(".json"), dict(policy_version=POLICY_VERSION,
                source_audio_sha256=file_sha256(source), sha256=file_sha256(cache), audio=wav_info(cache)))
            with self.assertRaisesRegex(ValueError, "PCM24"):
                builder.convert_audio(asset)


if __name__ == "__main__":
    unittest.main()
