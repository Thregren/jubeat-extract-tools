#!/usr/bin/env python3
"""Build a portable, provenance-preserving library from completed local exports.

Run as ``python -m workflows.normalize_library --help`` from this repository.
No original resource is modified. See docs/normalized-library.md for policies.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import threading
import time

from analysis.compare_versions import semantic_events, semantic_seq
from analysis.identity_relations import identity_key, resolve_identities

DIFFICULTIES = ("BSC", "ADV", "EXT")
POLICY_VERSION = "normalized-library-v1"
CHART_CONVERTER_VERSION = "tempo-source-order-v2"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if path.is_symlink() or temporary.is_symlink():
        raise ValueError("Refusing a symlink JSON destination/temporary file")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def file_sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def contained(root, value):
    """Reject paths escaping the caller-supplied root, including symlink escapes."""
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Path escapes input/output root: {value}")
    return path


def component(value):
    value = str(value)
    if not value or value in (".", "..") or "/" in value or "\\" in value:
        raise ValueError(f"Invalid directory component: {value!r}")
    return value


def wav_info(path):
    """Read RIFF PCM/float headers without Python-version-specific wave limitations."""
    path = Path(path)
    with path.open("rb") as source:
        header = source.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
            raise ValueError(f"Not a supported RIFF WAV: {path}")
        fmt = None
        payload = None
        while True:
            chunk = source.read(8)
            if not chunk:
                break
            if len(chunk) != 8:
                raise ValueError("Truncated WAV chunk header")
            kind, size = struct.unpack("<4sI", chunk)
            offset = source.tell()
            if offset + size > path.stat().st_size:
                raise ValueError("Truncated WAV payload")
            if kind == b"fmt ":
                if size < 16 or size > 4096:
                    raise ValueError("Unsupported WAV fmt size")
                data = source.read(size)
                tag, channels, rate, _, align, bits = struct.unpack_from("<HHIIHH", data)
                if tag == 65534:
                    if len(data) < 40 or data[28:40] != bytes.fromhex("00001000800000aa00389b71"):
                        raise ValueError("Unsupported WAV extensible subformat")
                    tag = struct.unpack_from("<I", data, 24)[0]
                if tag not in (1, 3) or not channels or not rate or not align:
                    raise ValueError("Unsupported WAV audio encoding")
                fmt = dict(format_tag=tag, channels=channels, sample_rate=rate,
                           block_align=align, bits_per_sample=bits)
            elif kind == b"data":
                payload = (offset, size)
            source.seek(offset + size + (size & 1))
        if not fmt or payload is None or payload[1] % fmt["block_align"]:
            raise ValueError("Missing/misaligned WAV fmt/data")
    return dict(fmt, frames=payload[1] // fmt["block_align"], data_bytes=payload[1],
                bytes=path.stat().st_size)


def native_parameters(info):
    return {key: info[key] for key in ("sample_rate", "channels", "frames")}


def itunes_gapless_samples(value):
    """Parse the standard iTunSMPB reserved/priming/padding/valid-sample fields."""
    if not isinstance(value, str):
        return None
    fields = value.split()
    if (len(fields) < 4 or any(not re.fullmatch(r"[0-9A-Fa-f]{8}", x) for x in fields[:3]) or
            not re.fullmatch(r"[0-9A-Fa-f]{16}", fields[3]) or int(fields[0], 16) != 0):
        return None
    priming, padding, samples = (int(x, 16) for x in fields[1:4])
    if samples <= 0:
        return None
    return dict(priming_samples=priming, padding_samples=padding, valid_samples=samples)


def compressed_audio_parameters(decoded):
    """Interpret a bounded ffprobe result, distinguishing AAC coding delay."""
    streams = decoded.get("streams", [])
    if not streams:
        raise ValueError("No source audio stream")
    stream = streams[0]
    codec = stream.get("codec_name")
    if codec not in ("aac", "flac"):
        raise ValueError(f"Unverified compressed source codec: {codec}")
    rate, channels = int(stream["sample_rate"]), int(stream["channels"])
    boundary = 0 if codec == "flac" else 2048 if "HE" in stream.get("profile", "") else 1024

    def nonnegative(value):
        try:
            return max(0, int(value))
        except (ValueError, TypeError):
            return 0

    side_data = [side for packet in decoded.get("packets", []) for side in packet.get("side_data_list", [])]
    priming = max([nonnegative(stream.get("initial_padding"))] +
                  [nonnegative(side.get("skip_samples")) for side in side_data])
    padding = max([0] + [nonnegative(side.get("discard_padding")) for side in side_data])
    tolerance = priming + max(boundary, padding)
    frames = None
    duration_frames = None
    validation = "decoded-output frame count only"
    try:
        if stream.get("duration_ts") is not None and stream.get("time_base"):
            duration_frames = round(int(stream["duration_ts"]) * Fraction(stream["time_base"]) * rate)
        elif stream.get("duration") or decoded.get("format", {}).get("duration"):
            seconds = stream.get("duration") or decoded["format"]["duration"]
            duration_frames = round(Fraction(seconds) * rate)
            tolerance = max(tolerance, 1)
    except (ValueError, TypeError, ZeroDivisionError):
        pass
    tags = decoded.get("format", {}).get("tags", {})
    smpb_tag = next((value for key, value in tags.items() if key.casefold() == "itunsmpb"), None)
    smpb = itunes_gapless_samples(smpb_tag) if codec == "aac" else None
    if smpb:
        frames, tolerance = smpb["valid_samples"], 0
        validation = "exact iTunSMPB valid samples (fourth hexadecimal field)"
    elif str(stream.get("nb_samples", "")).isdigit():
        frames, tolerance, validation = int(stream["nb_samples"]), 0, "ffprobe nb_samples"
    elif duration_frames is not None:
        frames = duration_frames
        validation = ("ffprobe container duration; declared initial/packet skip samples plus codec-frame boundary"
                      if codec == "aac" else "ffprobe FLAC duration/time_base")
    result = dict(codec_name=codec, sample_rate=rate, channels=channels, frames=frames,
                  frame_tolerance=tolerance, frame_validation=validation, probe=decoded,
                  initial_padding_samples=priming, packet_discard_padding_samples=padding,
                  codec_frame_boundary=boundary, container_duration_frames=duration_frames)
    if smpb:
        result["itunes_gapless"] = dict(smpb, container_frames_minus_valid=(duration_frames - frames)
                                        if duration_frames is not None else None,
                                        priming_plus_padding=smpb["priming_samples"] + smpb["padding_samples"])
    elif smpb_tag is not None:
        result["itunes_gapless_tag_status"] = "malformed/unusable; fallback sample/duration policy applied"
    return result


class LibraryBuilder:
    def __init__(self, root, output, comparison, *, relations=None,
                 alias_audio_audit=None, ffmpeg="ffmpeg", ffprobe="ffprobe", workers=1):
        self.root = Path(root).resolve()
        self.output = Path(output).resolve()
        self.comparison_path = Path(comparison).resolve()
        if self.output == self.root or self.root.is_relative_to(self.output):
            raise ValueError("Output must be a new library directory, not the source or its ancestor")
        self.relations_path = Path(relations).resolve() if relations else None
        self.alias_audio_path = Path(alias_audio_audit).resolve() if alias_audio_audit else None
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.workers = workers
        self.audio_index = {}
        self.hash_cache = {}
        self.probe_cache = {}
        self.cover_hash_cache = {}
        self.lock = threading.Lock()
        self.audio_lock = threading.Lock()

    def source(self, value):
        return contained(self.root, value)

    def destination(self, value):
        return contained(self.output, value)

    def write_output(self, value, data):
        write_json(self.destination(value), data)

    def cover_hash(self, observation):
        value = observation.get("cover")
        if not value:
            return None
        if value not in self.cover_hash_cache:
            path = self.source(value)
            with path.open("rb") as source:
                signature = source.read(8)
            self.cover_hash_cache[value] = file_sha256(path) if signature == b"\x89PNG\r\n\x1a\n" else None
        return self.cover_hash_cache[value]

    def existing(self, folder, value):
        if not value:
            return None
        path = self.source(folder / value)
        return str(path.relative_to(self.root)) if path.is_file() else None

    def media_row(self, version, row, observation, layer):
        folder = self.source(version["directory"])
        number, song_id = version["number"], observation["id"]
        songroot = folder / (f"mods/{layer}/songs/{song_id}" if layer.startswith("mod_")
                             else f"songs/{song_id}")
        result = dict(audio={}, cover=None, metadata=row.get("metadata", {}),
                      title=row.get("title"), artist=row.get("artist"),
                      index_only_reason="no playable chart/decoded audio/cover; retain original resource index")
        if any(str(asset.get("path", "")).lower().endswith((".wmv", ".mp4", ".avi"))
               for asset in row.get("assets", [])):
            result["index_only_reason"] = "non-song video; retain original package index"

        def audio(kind, path, resource_hash=None, family="wav", native=None, probe=None):
            if path:
                result["audio"][kind] = dict(path=path, source_sha256=resource_hash,
                                             family=family, native=native or {}, probe=probe or {})

        if number <= 4:
            for kind in ("bgm", "preview"):
                entry = row.get("selected_resources", {}).get(kind + ".bmpaudio.bin", {})
                audio(kind, self.existing(folder, entry.get("decoded_wav")),
                      entry.get("sha256"), "bmp", entry.get("audio"))
            candidates = sorted(row.get("covers", []), key=lambda c: -(c.get("width", 0) * c.get("height", 0)))
            for candidate in candidates:
                if candidate.get("width") == candidate.get("height") and candidate.get("width", 0) > 0:
                    result["cover"] = self.existing(folder, candidate.get("path"))
                    if result["cover"]:
                        break
        elif 5 <= number <= 9:
            detail = self.source(folder / row["path"])
            row = read_json(detail)
            result["metadata"] = row.get("metadata", row)
            songroot = detail.parent
            for entry in row.get("audio", []):
                audio(entry["kind"], self.existing(songroot, entry.get("output")),
                      entry.get("source_sha256"), "bmp", native_parameters(entry)
                      if all(k in entry for k in ("sample_rate", "channels", "frames")) else {})
        else:
            for kind in ("bgm", "preview"):
                entry = row.get("audio", {}).get("idx" if number == 10 and kind == "preview" else kind, {})
                resource_hash = entry.get("raw_sha256") or entry.get("sha256")
                if number == 10:
                    suffix = "bgm.s3v" if kind == "bgm" else "idx.s3v"
                    resource_hash = next((a.get("sha256") for a in row.get("assets", [])
                                          if a.get("path", "").endswith(suffix)), resource_hash)
                audio(kind, self.existing(folder, entry.get("path") or entry.get("output")),
                      resource_hash, "aac" if number >= 16 else "s3v" if number == 10 else "bmp",
                      {k: entry[k] for k in ("frames", "sample_rate", "channels") if k in entry},
                      entry.get("probe") or entry.get("output_probe"))
        if result["cover"] is None:
            result["cover"] = self.existing(songroot, "images/artwork.png" if number >= 16 else "cover.png")
        return result

    def build_plan(self):
        report = read_json(self.comparison_path)
        if report.get("sample") or report.get("completion") != "complete" or report.get("issues"):
            raise ValueError("A completed, non-sample, issue-free comparison report is required")
        observations, versions = [], []
        for version in report["versions"]:
            component(version["directory"])
            if self.output.is_relative_to(self.source(version["directory"])):
                raise ValueError("Output cannot be inside an original version directory")
            manifest = self.source(version["manifest"])
            if file_sha256(manifest) != version["manifest_sha256"]:
                raise ValueError(f"Comparison manifest is stale: {version['directory']}")
            rows = read_json(manifest)
            rows = rows.get("songs", []) if isinstance(rows, dict) else rows
            by_id = {(str(row.get("id", row.get("song_id"))), row.get("layer", "mobile" if
                      version["number"] >= 16 else "official")): row for row in rows}
            count = 0
            for song in version["songs"]:
                component(song["id"])
                if song["platform"] not in ("arcade", "mobile"):
                    raise ValueError("Unknown observation platform")
                for layer, charts in song["layers"].items():
                    component(layer)
                    if layer.startswith("archive_"):
                        continue
                    row = by_id.get((song["id"], layer))
                    if row is None:
                        raise ValueError(f"Manifest observation not found: {version['directory']} {song['id']} {layer}")
                    if any(not c.get("semantic_available") or not c.get("raw_sha256") or
                           not c.get("gameplay_sha256") for c in charts.values()):
                        raise ValueError("A chart lacks verified raw/event identity")
                    media = self.media_row(version, row, song, layer)
                    observations.append(dict(version=version["directory"], number=version["number"],
                                             kind=version["kind"], id=song["id"], platform=song["platform"],
                                             source_manifest=version["manifest"],
                                             observed_placeholder=bool(song.get("placeholder")),
                                             layer=layer, key=identity_key(version["directory"], song["id"], song["platform"]),
                                             charts=charts, playable=any(c.get("playable") for c in charts.values()), **media))
                    count += 1
            versions.append(dict(directory=version["directory"], kind=version["kind"], observations=count))
            print("indexed", version["directory"], count, flush=True)
        union, identity_report = resolve_identities(
            observations, read_json(self.relations_path) if self.relations_path else None,
            read_json(self.alias_audio_path) if self.alias_audio_path else None, cover_hash=self.cover_hash)
        groups = collections.defaultdict(list)
        for observation in observations:
            groups[union.get(observation["key"])].append(observation)
        packages, mappings, excluded = [], [], []
        for group in groups.values():
            group.sort(key=lambda o: (2 if o["platform"] == "mobile" else 1 if o["layer"].startswith("mod_") else 0,
                                     o["number"], o["id"]))
            playable = [o for o in group if o["playable"]]
            pool = playable or [o for o in group if o["audio"] or o["charts"] or o["cover"]]
            if not pool:
                excluded.extend(dict(version=o["version"], id=o["id"], layer=o["layer"],
                                     manifest=o["source_manifest"], observed_placeholder=o["observed_placeholder"],
                                     reason=o["index_only_reason"]) for o in group)
                continue
            identity = pool[0]["key"]
            title = next((o["title"] for o in group if o.get("title")), None)
            artist = next((o["artist"] for o in group if o.get("artist")), None)
            bundles = collections.OrderedDict()
            for observation in pool:
                signature = tuple(observation["charts"].get(d, {}).get("gameplay_sha256") for d in DIFFICULTIES)
                if not playable:
                    signature += tuple(observation["audio"].get(k, {}).get("source_sha256") or
                                       observation["audio"].get(k, {}).get("path") for k in ("bgm", "preview"))
                bundles.setdefault(signature, []).append(observation)
            first_path = None
            for index, (signature, same) in enumerate(bundles.items()):
                chosen = same[0]
                audio, cover = dict(chosen["audio"]), chosen["cover"]
                provenance = {kind: dict(version=chosen["version"], id=chosen["id"], path=value["path"], borrowed=False)
                              for kind, value in audio.items()}
                provenance["cover"] = dict(version=chosen["version"], id=chosen["id"], path=cover, borrowed=False) if cover else None
                for other in same:
                    for kind, value in other["audio"].items():
                        if kind not in audio:
                            audio[kind] = value
                            provenance[kind] = dict(version=other["version"], id=other["id"], path=value["path"], borrowed=True)
                for other in group:
                    if cover is None and other["cover"]:
                        cover = other["cover"]
                        provenance["cover"] = dict(version=other["version"], id=other["id"], path=cover, borrowed=True)
                category = ("首次收录" if index == 0 else "谱面变化") if playable else "特殊或不完整资源"
                folder = chosen["version"] + ("_Omnimix" if chosen["layer"].startswith("mod_") else "")
                suffix = "__" + fingerprint(signature)[:16] if index else ""
                path = f"{folder}/{category}/{component(identity.replace(':', '_'))}{suffix}"
                first_path = first_path or path
                missing_charts = [d for d in DIFFICULTIES if d not in chosen["charts"]]
                missing = [k for k in ("bgm", "preview") if k not in audio] + ([] if cover else ["cover"])
                missing += ["chart:" + d for d in missing_charts]
                package = dict(path=path, identity=identity, title=title, artist=artist,
                               category=category, first_path=first_path, bundle_signature=list(signature),
                               source={k: chosen[k] for k in ("version", "number", "id", "platform", "layer", "kind")},
                               charts=chosen["charts"], audio=audio, cover=cover, asset_provenance=provenance,
                               missing_charts=missing_charts, missing=missing,
                               nonplayable_charts=[d for d, c in chosen["charts"].items() if not c.get("playable")],
                               metadata=chosen["metadata"], aliases=[{k: o[k] for k in
                               ("version", "id", "layer", "title", "artist")} for o in group],
                               observations=[{k: o[k] for k in ("version", "id", "layer", "charts", "audio", "cover")} for o in same])
                package["fingerprint"] = fingerprint(dict(policy=POLICY_VERSION, package=package))
                packages.append(package)
                mappings.extend(dict(version=o["version"], id=o["id"], layer=o["layer"], identity=identity,
                                     target=path, selected=o is chosen, reason="first_bundle" if o is chosen else
                                     "same_gameplay_bundle", raw_chart_sha256={d: c["raw_sha256"] for d, c in o["charts"].items()}) for o in same)
            for observation in group:
                if observation not in pool:
                    mappings.append(dict(version=observation["version"], id=observation["id"], layer=observation["layer"],
                                         identity=identity, target=first_path, selected=False, reason="incomplete_or_placeholder_observation"))
        packages.sort(key=lambda p: (p["source"]["number"], p["source"]["layer"] != "official", p["path"]))
        if len({p["path"] for p in packages}) != len(packages):
            raise ValueError("Duplicate package destinations in plan")
        inputs = dict(comparison_sha256=file_sha256(self.comparison_path),
                      relations_sha256=file_sha256(self.relations_path) if self.relations_path else None,
                      alias_audio_audit_sha256=file_sha256(self.alias_audio_path) if self.alias_audio_path else None)
        plan = dict(schema=1, policy_version=POLICY_VERSION, source_root=str(self.root),
                    output=str(self.output), inputs=inputs, versions=versions,
                    policy=dict(audio="PCM24 WAV; native rate/channels/frames; no gain, resampling or generated preview",
                                charts="MC convenience + exact 300Hz JSON + unmodified original EVE/SEQ",
                                identity=identity_report["mode"], priority="official arcade, then mod, then mobile",
                                dedup="same identity and same gameplay hash bundle; HAKU/MEASURE-only changes do not repeat",
                                supplement="audio only from same gameplay bundle; cover from admitted identity with provenance"),
                    packages=packages, mappings=mappings, excluded_index_only=excluded,
                    excluded_placeholders=excluded,  # Historical field name; not a claim that every item is dummy.
                    summary=dict(identities=len({p["identity"] for p in packages if p["category"] != "特殊或不完整资源"}),
                                 playable_identities=len({p["identity"] for p in packages if p["category"] != "特殊或不完整资源"}),
                                 all_resource_identities=len({p["identity"] for p in packages}), packages=len(packages),
                                 playable_packages=sum(p["category"] != "特殊或不完整资源" for p in packages),
                                 chart_variants=sum(p["category"] == "谱面变化" for p in packages),
                                 incomplete_packages=sum(p["category"] == "特殊或不完整资源" for p in packages),
                                 observations=len(observations), placeholder_only_observations=len(excluded),
                                 index_only_observations=len(excluded),
                                 accepted_identity_relations=len(identity_report["accepted"]),
                                 rejected_identity_relations=len(identity_report["rejected"])))
        plan["fingerprint"] = fingerprint(plan)
        self.output.mkdir(parents=True, exist_ok=True)
        marker = self.destination("library.json")
        if marker.exists():
            previous = read_json(marker)
            if previous.get("policy_version") != POLICY_VERSION or previous.get("plan_fingerprint") != plan["fingerprint"]:
                raise ValueError("Output belongs to a different plan/policy; use a fresh --output directory")
        elif any(self.output.iterdir()):
            raise ValueError("Refusing a nonempty output directory without this tool's library.json marker")
        self.write_output(marker, dict(schema=1, policy_version=POLICY_VERSION, plan_fingerprint=plan["fingerprint"], source_root=str(self.root)))
        self.write_output("_reports/生成计划.json", plan)
        self.write_output("_reports/身份关系验证.json", identity_report)
        self.write_output("_reports/来源与去重映射.json", dict(mappings=mappings, excluded_index_only=excluded,
                            excluded_placeholders=excluded, historical_field_note="excluded_placeholders is a compatibility alias, not a dummy classification", policy=plan["policy"]))
        print(json.dumps(plan["summary"], ensure_ascii=False), flush=True)
        return plan

    def cached_hash(self, path):
        stat = path.stat()
        key = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        with self.lock:
            value = self.hash_cache.get(key)
        if value is None:
            value = file_sha256(path)
            with self.lock:
                self.hash_cache[key] = value
        return value

    def source_audio_parameters(self, asset):
        source = self.source(asset["path"])
        stat = source.stat()
        key = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        with self.lock:
            cached = self.probe_cache.get(key)
        if cached:
            return cached
        with source.open("rb") as stream:
            magic = stream.read(12)
        if magic[:4] == b"RIFF" and magic[8:] == b"WAVE":
            actual = wav_info(source)
            result = dict(codec_name="pcm", **native_parameters(actual), frame_tolerance=0,
                          frame_validation="exact WAV data size")
        else:
            executable = shutil.which(self.ffprobe)
            if not executable and self.ffprobe == "ffprobe" and shutil.which(self.ffmpeg):
                sibling = Path(shutil.which(self.ffmpeg)).with_name("ffprobe")
                executable = str(sibling) if sibling.is_file() else None
            if not executable:
                raise ValueError("ffprobe is required to validate compressed source audio")
            command = [executable, "-v", "error", "-select_streams", "a:0", "-read_intervals", "%+#1",
                       "-show_packets", "-show_entries",
                       "stream=codec_name,profile,sample_rate,channels,duration_ts,time_base,duration,nb_samples,initial_padding:"
                       "packet=pts,duration:packet_side_data=side_data_type,skip_samples,discard_padding:"
                       "format=duration:format_tags=iTunSMPB",
                       "-of", "json", str(source)]
            probe = subprocess.run(command, capture_output=True, text=True)
            if probe.returncode:
                raise ValueError("ffprobe failed: " + probe.stderr[-3000:])
            result = compressed_audio_parameters(json.loads(probe.stdout))
        with self.lock:
            self.probe_cache[key] = result
        return result

    def validate_audio(self, asset, info):
        source_info = self.source_audio_parameters(asset)
        if info["format_tag"] != 1 or info["bits_per_sample"] != 24 or info["frames"] <= 0:
            raise ValueError("PCM24 cache is required")
        if any(info[k] != source_info[k] for k in ("sample_rate", "channels")):
            raise ValueError("PCM24 output must preserve the actual source rate/channels")
        if source_info["frames"] is not None and abs(info["frames"] - source_info["frames"]) > source_info["frame_tolerance"]:
            raise ValueError("PCM24 frame count disagrees with source sample count/duration")
        for key, expected in asset.get("native", {}).items():
            if key in ("sample_rate", "channels", "frames") and int(expected) != info[key]:
                raise ValueError(f"Decoded output disagrees with declared native {key}: {asset['path']}")
        streams = asset.get("probe", {}).get("streams", [])
        if streams:
            for key in ("sample_rate", "channels"):
                if streams[0].get(key) is not None and int(streams[0][key]) != source_info[key]:
                    raise ValueError(f"Source WAV disagrees with probe {key}")
        return source_info

    def convert_audio(self, asset):
        source = self.source(asset["path"])
        source_hash = self.cached_hash(source)
        key = fingerprint(dict(source_audio_sha256=source_hash, policy=POLICY_VERSION))
        cache = self.destination(f"_cache/audio/{key}.wav")
        sidecar = self.destination(cache.with_suffix(".json"))
        cache.parent.mkdir(parents=True, exist_ok=True)
        # Different source paths can be hardlinks with identical content. Serialize
        # cache publication only; heavy ffmpeg work runs at the requested concurrency.
        with self.audio_lock:
            if cache.is_file() and sidecar.is_file():
                saved = read_json(sidecar)
                actual = wav_info(cache)
                parameters = self.validate_audio(asset, actual)
                if (saved.get("policy_version") != POLICY_VERSION or saved.get("source_audio_sha256") != source_hash or
                        saved.get("sha256") != self.cached_hash(cache) or saved.get("audio") != actual):
                    raise ValueError(f"Audio cache integrity mismatch: {cache}")
                saved.update(source=asset, source_parameters=parameters)
                self.write_output(sidecar, saved)
                return saved
        executable = shutil.which(self.ffmpeg)
        if not executable:
            raise ValueError("ffmpeg is required for --run; install it or pass --ffmpeg PATH")
        temporary = cache.with_name(f".{key}.{threading.get_ident()}.tmp.wav")
        temporary.unlink(missing_ok=True)
        command = [executable, "-nostdin", "-v", "warning", "-i", str(source), "-map", "0:a:0",
                   "-map_metadata", "-1", "-c:a", "pcm_s24le", "-threads", "1", str(temporary)]
        try:
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode:
                raise ValueError(f"ffmpeg failed: {result.stderr[-3000:]}")
            actual = wav_info(temporary)
            source_parameters = self.validate_audio(asset, actual)
            saved = dict(policy_version=POLICY_VERSION, cache=str(cache.relative_to(self.output)),
                         source_audio_sha256=source_hash, source_parameters=source_parameters,
                         audio=actual, sha256=file_sha256(temporary),
                         warning=result.stderr.strip(), source=asset)
            with self.audio_lock:
                if cache.exists():
                    if file_sha256(cache) != saved["sha256"]:
                        raise ValueError("Concurrent audio cache content collision")
                    temporary.unlink()
                else:
                    temporary.replace(cache)
                self.write_output(sidecar, saved)
            return saved
        finally:
            temporary.unlink(missing_ok=True)

    def place(self, source, destination):
        destination = self.destination(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if file_sha256(source) == file_sha256(destination):
                return
            destination.unlink()  # Only owned library outputs; never an original source.
        try:
            os.link(source, destination)
        except OSError:
            shutil.copyfile(source, destination)

    def render_chart(self, raw, code, fmt, package):
        from assets.eve import parse_eve_chart
        from mobile.extract import parse_chart, write_chart_mc
        if fmt == "seq":
            chart = parse_chart(raw)
            notes, timing = chart["notes"], chart["timing"]
        else:
            notes, timing, offset = parse_eve_chart(raw, code)
            if offset:
                raise ValueError("Unexpected nonzero EVE offset")
        mc = json.loads(write_chart_mc(notes, timing, package["title"] or "ID " + package["source"]["id"], package["artist"] or "",
                                      {"BSC": "bas", "ADV": "adv", "EXT": "ext"}[code],
                                      package["charts"][code].get("level") if package["charts"][code].get("level") is not None else "?",
                                      "../cover.png" if package["cover"] else "",
                                      "../bgm.wav" if "bgm" in package["audio"] else ""))
        mc["meta"].update(time=0, source_format=fmt, precise_events=code + ".events.json")
        return mc, len(notes), sum(n[2] is not None for n in notes)

    def exact_events(self, raw, fmt):
        if fmt == "eve":
            from assets.eve import raw_events
            events = raw_events(raw)
            if events["unparsed_lines"]:
                raise ValueError("Unparsed raw EVE lines")
            return events
        if len(raw) < 96:
            raise ValueError("Truncated SEQ header")
        count = struct.unpack_from("<I", raw, 4)[0]
        if 96 + 8 * count > len(raw):
            raise ValueError("SEQ record bounds")
        names = {1: "PLAY", 2: "END", 3: "MEASURE", 4: "HAKU", 5: "TEMPO", 6: "LONG"}
        events = []
        for index in range(count):
            r0, r1 = struct.unpack_from("<II", raw, 96 + index * 8)
            events.append(dict(tick=r0 >> 8, command=names.get(r0 & 255, f"TYPE_{r0 & 255}"),
                               value=r1, arguments=[str(r1)], raw_r0=r0, raw_r1=r1))
        result = dict(tick_hz=300, header_hex=raw[:96].hex(), events=events,
                      trailing_bytes_hex=raw[96 + 8 * count:].hex(), unparsed_lines=[])
        rebuilt = bytes.fromhex(result["header_hex"]) + b"".join(struct.pack("<II", e["raw_r0"], e["raw_r1"])
                                                                         for e in events) + bytes.fromhex(result["trailing_bytes_hex"])
        if rebuilt != raw:
            raise ValueError("SEQ exact-event roundtrip mismatch")
        return result

    def reusable_package(self, package, done):
        if done.get("chart_converter_version") != CHART_CONVERTER_VERSION:
            return False
        if done.get("fingerprint") != package["fingerprint"]:
            return False
        folder = self.destination(package["path"])
        expected = {"provenance.json"}
        for kind, asset in package["audio"].items():
            expected.add(kind + ".wav")
            old = done.get("audio", {}).get(kind, {})
            current = self.audio_index[asset["path"]]
            if (old.get("source_audio_sha256") != current["source_audio_sha256"] or
                    old.get("sha256") != current["sha256"] or
                    old.get("source_parameters") != current.get("source_parameters")):
                return False
        if package["cover"]:
            expected.add("cover.png")
            if done.get("cover", {}).get("sha256") != file_sha256(self.source(package["cover"])):
                return False
        for code, chart in package["charts"].items():
            source = self.source(Path(package["source"]["version"]) / chart["raw_eve"])
            if file_sha256(source) != chart["raw_sha256"]:
                raise ValueError("Raw source chart changed since comparison")
            fmt = "seq" if source.suffix.lower() == ".seq" else "eve"
            expected.update((f"original/{code}.{fmt}", f"charts/{code}.events.json"))
            if chart.get("playable"):
                expected.add(f"charts/{code}.mc")
        if set(done.get("files", {})) != expected:
            return False
        for relative, record in done.get("files", {}).items():
            path = contained(folder, relative)
            if not path.is_file() or path.stat().st_size != record["bytes"] or file_sha256(path) != record["sha256"]:
                return False
            if relative in ("bgm.wav", "preview.wav"):
                self.validate_audio(package["audio"][relative[:-4]], wav_info(path))
        return bool(done.get("files"))

    def export_package(self, package):
        folder = self.destination(package["path"])
        done_path = self.destination(folder / "song.json")
        if done_path.is_file():
            done = read_json(done_path)
            if self.reusable_package(package, done):
                return done
        folder.mkdir(parents=True, exist_ok=True)
        result = {key: package[key] for key in ("identity", "title", "artist", "source", "category", "first_path",
                                                       "bundle_signature", "missing", "missing_charts", "nonplayable_charts", "fingerprint")}
        result.update(schema=1, policy_version=POLICY_VERSION,
                      chart_converter_version=CHART_CONVERTER_VERSION,
                      audio={}, charts={}, cover=None)
        files = []
        for kind, asset in package["audio"].items():
            info = self.audio_index[asset["path"]]
            self.validate_audio(asset, info["audio"])
            self.place(self.destination(info["cache"]), folder / (kind + ".wav"))
            result["audio"][kind] = info
            files.append(kind + ".wav")
        if package["cover"]:
            from PIL import Image
            source = self.source(package["cover"])
            with Image.open(source) as image:
                image.load()
                if image.format != "PNG":
                    raise ValueError("Cover must be an actual PNG")
                result["cover"] = dict(width=image.width, height=image.height, mode=image.mode,
                                       source=package["cover"], sha256=file_sha256(source))
            self.place(source, folder / "cover.png")
            files.append("cover.png")
        for code, chart in package["charts"].items():
            source = self.source(Path(package["source"]["version"]) / chart["raw_eve"])
            if source.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("Raw chart exceeds the comparison reader's 16 MiB bound")
            raw = source.read_bytes()
            raw_hash = hashlib.sha256(raw).hexdigest()
            fmt = "seq" if source.suffix.lower() == ".seq" else "eve"
            semantics = semantic_seq(raw) if fmt == "seq" else semantic_events(raw)
            if (raw_hash != chart["raw_sha256"] or not semantics.get("available") or
                    semantics.get("sha256") != chart["semantic_sha256"] or
                    semantics.get("gameplay_sha256") != chart["gameplay_sha256"]):
                raise ValueError(f"Source chart hash/event identity changed: {source}")
            original = f"original/{code}.{fmt}"
            self.place(source, folder / original)
            exact = self.exact_events(raw, fmt)
            exact.update(schema="jubeat-exact-events-v1", source_format=fmt, source_sha256=raw_hash,
                         semantic_sha256=chart["semantic_sha256"], gameplay_sha256=chart["gameplay_sha256"], difficulty=code)
            self.write_output(folder / f"charts/{code}.events.json", exact)
            commands = semantics["commands"]
            notes = commands.get("PLAY", 0) + commands.get("LONG", 0)
            holds = commands.get("LONG", 0)
            if chart.get("playable"):
                mc, rendered_notes, rendered_holds = self.render_chart(raw, code, fmt, package)
                if rendered_notes != notes or rendered_holds != holds:
                    raise ValueError("MC note/LONG count differs from original events")
                self.write_output(folder / f"charts/{code}.mc", mc)
                files.append(f"charts/{code}.mc")
            result["charts"][code] = dict(raw_sha256=raw_hash, semantic_sha256=chart["semantic_sha256"],
                                         gameplay_sha256=chart["gameplay_sha256"], notes=notes, holds=holds,
                                         exact_events=len(exact["events"]), playable=chart["playable"],
                                         mc_exported=bool(chart.get("playable")), level=chart.get("level"))
            files += [original, f"charts/{code}.events.json"]
        self.write_output(folder / "provenance.json", {key: package[key] for key in
                   ("aliases", "source", "asset_provenance", "metadata", "observations")})
        files.append("provenance.json")
        result["files"] = {name: dict(bytes=(folder / name).stat().st_size, sha256=file_sha256(folder / name)) for name in files}
        self.write_output(done_path, result)  # Completion marker is always last.
        return result

    def run(self, plan, limit=None):
        packages = plan["packages"][:limit] if limit else plan["packages"]
        # One task per referenced source path; equal actual WAV bytes share a cache.
        assets = {a["path"]: a for p in packages for a in p["audio"].values()}
        errors, warnings, results = [], [], []
        started = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self.convert_audio, asset): asset for asset in assets.values()}
            for future in concurrent.futures.as_completed(futures):
                asset = futures[future]
                try:
                    info = future.result()
                    self.audio_index[asset["path"]] = info
                    if info.get("warning"):
                        warnings.append(dict(stage="audio", source=asset["path"], warning=info["warning"]))
                except Exception as error:
                    errors.append(dict(stage="audio", source=asset["path"], error=str(error)))
                if (len(self.audio_index) + len(errors)) % 25 == 0:
                    print("audio", len(self.audio_index), "/", len(assets), "errors", len(errors), flush=True)
        for index, package in enumerate(packages):
            try:
                results.append(self.export_package(package))
            except Exception as error:
                errors.append(dict(stage="package", path=package["path"], error=str(error)))
            if (index + 1) % 25 == 0:
                print("packages", index + 1, "/", len(packages), "errors", len(errors), flush=True)
        summary = dict(schema=1, status="errors" if errors else "sample_complete" if limit else "complete",
                       plan_fingerprint=plan["fingerprint"], plan_summary=plan["summary"], sample=bool(limit),
                       selected_packages=len(packages), exported_packages=len(results), audio_sources=len(self.audio_index),
                       errors=errors, warnings=warnings, elapsed_seconds=round(time.time() - started, 2))
        self.write_output("_reports/生成结果.json", summary)
        self.write_output("_reports/歌曲索引.json", results)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return int(bool(errors))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="completed per-version export root")
    parser.add_argument("--output", type=Path, required=True, help="new normalized library directory")
    parser.add_argument("--comparison", type=Path, required=True, help="complete analysis.compare_versions JSON report")
    parser.add_argument("--relations", type=Path, help="optional audited identity-relations JSON")
    parser.add_argument("--alias-audio-audit", type=Path, help="optional approved arcade dual-audio alias audit JSON")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg executable name or path, resolved from PATH by default")
    parser.add_argument("--ffprobe", default="ffprobe", help="ffprobe executable name or path for compressed-source validation")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--plan", action="store_true", help="plan only (also the default without --run)")
    parser.add_argument("--run", action="store_true", help="build/verify plan and export packages")
    parser.add_argument("--limit", type=int, help="bounded export; result is always labeled sample_complete")
    args = parser.parse_args(argv)
    if args.workers < 1 or (args.limit is not None and args.limit < 1):
        parser.error("--workers/--limit must be positive")
    if args.limit and not args.run:
        parser.error("--limit requires --run")
    try:
        builder = LibraryBuilder(args.root, args.output, args.comparison, relations=args.relations,
                                 alias_audio_audit=args.alias_audio_audit, ffmpeg=args.ffmpeg,
                                 ffprobe=args.ffprobe, workers=args.workers)
        plan = builder.build_plan()
        return builder.run(plan, args.limit) if args.run else 0
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"normalize_library: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
