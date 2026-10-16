#!/usr/bin/env python3
"""jubeat plus / jukebeat 移动版备份 (.adbk) → 歌曲 / 谱面 / 封面 提取工具。

用法
----
    python3 mobile/extract.py --package-dir input --out output/mobile
    python3 mobile/extract.py --packages input/jukebeat.adbk --out output/demo

原理
----
.adbk 是 iMazing 备份（本质 zip），里面每首曲子是一个 `Documents/<ID>.jbt`；
.jbt 又是一个 zip，从中读取 info / artwork / seq_* / bgm 等已知条目。
这些资源使用 BFCodec 的 Blowfish 变种，密钥 = MD5("Konami Bemani Mobile iPad")。
解密算法整理自 Jubeat.app 3.9.11 的逆向记录，实现见 tools/jbf.c；
逐字节原理演示见 docs/walkthrough.md。
"""
from __future__ import annotations

import argparse
import bisect
import ctypes
import io
import json
import math
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE / "tools"
sys.path.insert(0, str(TOOLS))

from cgbi import cgbi_to_png  # noqa: E402

TICK_HZ = 300
BEAT_SNAP = 48                      # 拍值取整粒度，与曲库既有谱面一致
KEY_HEX = "f9a142c70b07d9a8093b56b8c2eeb698"   # MD5("Konami Bemani Mobile iPad")

PACKAGES = {
    "jubeatplus": "jp.konami.jubeatplus.adbk",
    "jukebeat": "jp.konami.jukebeat.adbk",
}
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_SONG_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 100_000
_LIB = None
_LIB_LOCK = threading.Lock()

# 从长押起点到终点的坐标增量；index = x + 4*y，不以箭头朝向命名。
_DIR_VEC = {0: (0, -1), 1: (0, 1), 2: (-1, 0), 3: (1, 0)}

_SEQ_CODES = ("bas", "adv", "ext")
_CODE_NAME = {"bas": "BSC", "adv": "ADV", "ext": "EXT"}
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class DecryptError(RuntimeError):
    """密文无法解密（密钥/布局不匹配）。"""


def load_lib() -> ctypes.CDLL:
    """载入（必要时编译）jbf 解密库。"""
    if sys.platform not in {"darwin", "linux"}:
        raise DecryptError("自动编译支持 macOS / Linux；Windows 请使用 WSL")
    build = HERE.parent / ".build"
    build.mkdir(exist_ok=True)
    dylib = build / ("libjbf.dylib" if sys.platform == "darwin" else "libjbf.so")
    src = TOOLS / "jbf.c"
    if not dylib.exists() or dylib.stat().st_mtime < src.stat().st_mtime:
        cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
        if not cc:
            raise DecryptError("找不到 C 编译器，无法编译 tools/jbf.c")
        flags = ["-dynamiclib"] if sys.platform == "darwin" else ["-shared", "-fPIC"]
        subprocess.run([cc, "-O2", *flags, "-o", str(dylib), str(src)],
                       check=True, timeout=120)
    lib = ctypes.CDLL(str(dylib))
    lib.jbf_decrypt.restype = ctypes.c_long
    lib.jbf_decrypt.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                                ctypes.c_long, ctypes.c_char_p, ctypes.c_long]
    lib.jbf_setkey.restype = ctypes.c_long
    lib.jbf_setkey.argtypes = [ctypes.c_char_p, ctypes.c_int]
    return lib


class Decryptor:
    def __init__(self) -> None:
        global _LIB
        self._key = ctypes.create_string_buffer(bytes.fromhex(KEY_HEX), 16)
        # 只初始化一次，避免另一个 Decryptor 重写正在被工作线程读取的轮密钥。
        with _LIB_LOCK:
            if _LIB is None:
                lib = load_lib()
                if lib.jbf_setkey(self._key, 16) != 0:
                    raise DecryptError("jbf_setkey 失败")
                _LIB = lib
            self._lib = _LIB

    def __call__(self, blob: bytes) -> bytes:
        if not blob:
            return b""
        if len(blob) > MAX_MEMBER_BYTES:
            raise DecryptError("条目超过 64 MiB 上限")
        out = ctypes.create_string_buffer(len(blob) + 64)
        n = self._lib.jbf_decrypt(self._key, 16, blob, len(blob), out, len(out))
        if n < 0:
            raise DecryptError("jbf_decrypt 失败 (%d)" % n)
        return out.raw[:n]


def _truncate(value: Fraction, places: int) -> Fraction:
    scale = Fraction(10) ** places
    return Fraction(math.floor(value * scale), scale)


def value_to_bpm(value: int) -> Fraction:
    """TEMPO 记录值 → BPM。与街机 IFS / 曲库既有转法一致（截断 + 回推）。"""
    if value <= 0:
        raise ChartError("TEMPO 必须大于 0")
    exact = Fraction(6 * 10 ** 7, value)
    places = 0
    while places <= 8:
        cand = _truncate(exact, places)
        if cand > 0 and 6 * 10 ** 7 / cand < value + 1:
            return cand
        places += 1
    return exact


class _TimeMap:
    def __init__(self, points: list[tuple[Fraction, Fraction]]) -> None:
        self.changes: list[tuple[Fraction, Fraction, Fraction]] = []
        beat = Fraction(0)
        prev: tuple[Fraction, Fraction] | None = None
        for sec, bpm in points:
            if prev is not None:
                beat += prev[1] * (sec - prev[0]) / 60
            self.changes.append((sec, beat, bpm))
            prev = (sec, bpm)
        self._secs = [c[0] for c in self.changes]

    def beats_at(self, seconds: Fraction) -> Fraction:
        i = max(0, bisect.bisect_right(self._secs, seconds) - 1)
        sec, beat, bpm = self.changes[i]
        return beat + bpm * (seconds - sec) / 60


class ChartError(ValueError):
    """谱面无法解析。"""


def _round_beats(beat: Fraction, denom: int = BEAT_SNAP) -> Fraction:
    return Fraction(round(beat * denom), denom)


def parse_seq(data: bytes) -> tuple[bytes, int, int, int, int, list]:
    if len(data) < 0x60:
        raise ChartError("seq 太小 (%d 字节)" % len(data))
    magic = bytes(data[:4])
    cnt, notecnt, total = struct.unpack_from("<III", data, 4)
    if 0x60 + cnt * 8 > len(data):
        raise ChartError("seq 记录表越界")
    mask = struct.unpack_from("<H", data, 18)[0]
    recs = []
    for i in range(cnt):
        r0, r1 = struct.unpack_from("<II", data, 0x60 + 8 * i)
        recs.append((r0 & 0xFF, r0 >> 8, r1))
    return magic, cnt, notecnt, total, mask, recs


def parse_chart(data: bytes) -> dict:
    """seq_* 明文 → {notes, timing, ...}。notes = (beat, index, endbeat, endindex)。"""
    magic, cnt, notecnt, total, mask, recs = parse_seq(data)
    # Keep the source's final TEMPO at each tick, as for arcade EVE charts.
    # Sorting equal-tick records by their values changes the effective BPM.
    tempo_by_tick = {}
    for kind, tick, value in recs:
        if kind == 5:
            tempo_by_tick[tick] = value
    tempos = sorted(tempo_by_tick.items())
    if not tempos:
        raise ChartError("没有 TEMPO 记录")
    if tempos[0][0] != 0:
        raise ChartError("首个 TEMPO 必须位于 tick 0")
    tm = _TimeMap([(Fraction(t, TICK_HZ), value_to_bpm(v)) for t, v in tempos])
    notes = []
    for kind, tick, value in recs:
        if kind == 1:                                   # 单点
            if not 0 <= value < 16:
                raise ChartError("单点位置超出 4×4 面板")
            beat = _round_beats(tm.beats_at(Fraction(tick, TICK_HZ)))
            notes.append((beat, value, None, None))
        elif kind == 6:                                 # 长押
            index = value & 15
            direction = (value >> 4) & 3
            length = (value >> 6) & 3
            duration = value >> 8
            raw = tm.beats_at(Fraction(tick, TICK_HZ))
            beat = _round_beats(raw)
            span = _round_beats(tm.beats_at(Fraction(tick + duration, TICK_HZ)) - raw)
            dx, dy = _DIR_VEC[direction]
            x, y = index % 4, index // 4
            notes.append((beat, index, beat + span,
                          (x + dx * length) + 4 * (y + dy * length)))
    notes.sort(key=lambda n: (n[0], n[1]))
    timing = [(_round_beats(beat), bpm) for _, beat, bpm in tm.changes]
    return {"magic": magic, "count": cnt, "header_notes": notecnt, "total_tick": total,
            "mask": mask, "notes": notes, "timing": timing}


def beat_tuple(beat: Fraction) -> list[int]:
    whole = int(beat)
    frac = beat - whole
    return [whole, 0, 1] if frac == 0 else [whole, frac.numerator, frac.denominator]


def volume(notes: list) -> tuple[int, int]:
    """物量口径：单点 ×1 + 长押 ×2。返回 (物量, 长押数)。"""
    holds = sum(1 for n in notes if n[2] is not None)
    return len(notes) + holds, holds


def safe_name(text: str) -> str:
    cleaned = _ILLEGAL.sub("_", unicodedata.normalize("NFC", text)).strip().rstrip(".")
    while len(cleaned.encode("utf-8")) > 160:
        cleaned = cleaned[:-1]
    return cleaned or "untitled"


def write_chart_mc(notes: list, timing: list, title: str, artist: str,
                   code: str, level, cover: str, audio_name: str = "bgm.m4a") -> bytes:
    """产出与曲库同构的 .mc JSON。"""
    level = str(level)
    version = "%s Lv%s" % (_CODE_NAME[code], level)
    chart = {
        "meta": {
            "background": cover, "version": version, "id": 0, "mode": 4,
            "time": int(time.time()),
            "song": {"title": title, "artist": artist, "id": 0},
            "mode_ext": {}, "cover": cover, "level": level, "creator": "jubeat",
        },
        "time": [{"beat": beat_tuple(b), "bpm": float(bpm)} for b, bpm in timing],
        "note": ([{"beat": [0, 0, 1], "sound": audio_name, "type": 1,
                   "offset": 0, "vol": 100}] if audio_name else []),
        "extra": {version: {"divide": 4, "speed": 100, "save": 0,
                            "lock": 0, "edit_mode": 0}},
    }
    for beat, index, endbeat, endindex in notes:
        note = {"beat": beat_tuple(beat), "index": index}
        if endbeat is not None:
            note["endbeat"] = beat_tuple(endbeat)
            note["endindex"] = endindex
        chart["note"].append(note)
    return json.dumps(chart, ensure_ascii=False, separators=(",", ":")).encode()


def to_ogg(m4a: bytes) -> bytes:
    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError("找不到 ffmpeg，无法转 ogg")
    base = [ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", "pipe:0"]
    proc = subprocess.run(
        [*base, "-c:a", "libvorbis", "-q:a", "5", "-f", "ogg", "pipe:1"],
        input=m4a, capture_output=True, timeout=900)
    if proc.returncode and b"Unknown encoder 'libvorbis'" in proc.stderr:
        # Some ffmpeg distributions ship only the built-in Vorbis encoder.
        # Other input/encoding errors must not trigger a silent retry.
        proc = subprocess.run(
            [*base, "-c:a", "vorbis", "-strict", "-2", "-q:a", "5", "-f", "ogg", "pipe:1"],
            input=m4a, capture_output=True, timeout=900)
    if proc.returncode:
        raise RuntimeError("音频转码失败：" + proc.stderr.decode("utf-8", "replace")[-400:])
    return proc.stdout


def write_mcz(out_path: Path, title: str, artist: str, levels: dict,
              charts: dict, cover: bytes, audio: bytes, transcode: bool,
              cover_name: str) -> None:
    if transcode and audio:
        audio = to_ogg(audio)
    audio_name = "bgm.ogg" if transcode else "bgm.m4a"
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for code in _SEQ_CODES:
            if code not in charts:
                continue
            level = str(levels.get(code, 0))
            version = "%s Lv%s" % (_CODE_NAME[code], level)
            z.writestr("0/%s_%s.mc" % (safe_name(title), version),
                       write_chart_mc(charts[code]["notes"], charts[code]["timing"],
                                      title, artist, code, level,
                                      cover_name if cover else "",
                                      audio_name if audio else ""))
        if audio:
            z.writestr("0/" + audio_name, audio)
        if cover:
            z.writestr("0/" + cover_name, cover)


_ENTRY_JUNK = {"info", "infov2", "artwork", "artwork_s", "name_b", "name_w",
               "name_b_e", "name_w_e", "index", "seq_bas", "seq_adv", "seq_ext", "bgm"}


def bounded_read(archive: zipfile.ZipFile, name: str) -> bytes:
    item = archive.getinfo(name)
    if item.file_size > MAX_MEMBER_BYTES:
        raise ChartError("归档条目超过 64 MiB 上限")
    with archive.open(item) as handle:
        data = handle.read(MAX_MEMBER_BYTES + 1)
    if len(data) > MAX_MEMBER_BYTES:
        raise ChartError("归档解压体积超过上限")
    return data


def process_song(blob: bytes, tune_id: str, decrypt: Decryptor) -> dict:
    """解一首曲子，返回内存中的全部产物 + 元数据。"""
    with zipfile.ZipFile(io.BytesIO(blob)) as inner:
        if len(inner.infolist()) > 100 or sum(i.file_size for i in inner.infolist()) > MAX_SONG_BYTES:
            raise ChartError("歌曲归档超出条目/体积预算")
        entries = {}
        for i in inner.infolist():
            if i.filename not in _ENTRY_JUNK:
                continue
            entries[i.filename] = decrypt(bounded_read(inner, i.filename))
    raw_info = entries.get("infov2") or entries.get("info")
    if not raw_info:
        raise ChartError("缺少 info/infov2")
    info = plistlib.loads(raw_info)
    title = str(info.get("Name") or tune_id)
    artist = str(info.get("Artist") or "")
    levels = {"bas": info.get("LvBas", 0), "adv": info.get("LvAdv", 0),
              "ext": info.get("LvExt", 0)}
    charts, stats = {}, {}
    for code in _SEQ_CODES:
        data = entries.get("seq_" + code)
        if not data:
            raise ChartError("缺少谱面 seq_" + code)
        chart = parse_chart(data)
        charts[code] = chart
        vol, holds = volume(chart["notes"])
        stats[code] = {
            "level": levels.get(code, 0), "notes": len(chart["notes"]),
            "holds": holds, "volume": vol,
            "bpm": round(float(chart["timing"][0][1]), 4) if chart["timing"] else 0.0,
        }
    images = {}
    for key in ("artwork", "artwork_s", "name_b", "name_w", "name_b_e", "name_w_e"):
        if key in entries:
            try:
                images[key] = cgbi_to_png(entries[key])
            except ValueError:
                pass
    return {
        "id": tune_id, "title": title, "artist": artist,
        "yomi": str(info.get("NameYomi") or ""),
        "info": {k: v for k, v in info.items() if isinstance(v, (str, int, float, bool))},
        "charts": charts, "stats": stats, "images": images,
        "audio": entries.get("bgm"), "preview": entries.get("index"),
    }


def dump_song(song: dict, song_dir: Path, mcz: bool, transcode: bool) -> None:
    song_dir.mkdir(parents=True, exist_ok=True)
    title = song["title"]
    safe = safe_name(title)
    cover_name = "artwork.png" if song["images"].get("artwork") else ""
    if song["images"].get("artwork"):
        (song_dir / "artwork.png").write_bytes(song["images"]["artwork"])
    if song["images"].get("artwork_s"):
        (song_dir / "artwork_s.png").write_bytes(song["images"]["artwork_s"])
    for key, fname in (("name_b", "name_b.png"), ("name_w", "name_w.png"),
                       ("name_b_e", "name_b_en.png"), ("name_w_e", "name_w_en.png")):
        if song["images"].get(key):
            (song_dir / fname).write_bytes(song["images"][key])
    if song["audio"]:
        (song_dir / "bgm.m4a").write_bytes(song["audio"])
    if song["preview"]:
        (song_dir / "preview.m4a").write_bytes(song["preview"])
    for code, chart in song["charts"].items():
        level = song["stats"][code]["level"]
        fname = "%s Lv%s.mc" % (_CODE_NAME[code], level)
        (song_dir / fname).write_bytes(
            write_chart_mc(chart["notes"], chart["timing"], title, song["artist"],
                           code, level, cover_name, "bgm.m4a" if song["audio"] else ""))
    meta = {
        "id": song["id"], "title": title, "artist": song["artist"], "yomi": song["yomi"],
        "levels": {"BSC": song["stats"].get("bas", {}).get("level", 0),
                   "ADV": song["stats"].get("adv", {}).get("level", 0),
                   "EXT": song["stats"].get("ext", {}).get("level", 0)},
        "stats": song["stats"], "info": song["info"],
    }
    (song_dir / "info.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if mcz:
        write_mcz(song_dir / (safe + ".mcz"), title, song["artist"],
                  {"bas": song["stats"].get("bas", {}).get("level", 0),
                   "adv": song["stats"].get("adv", {}).get("level", 0),
                   "ext": song["stats"].get("ext", {}).get("level", 0)},
                  song["charts"], song["images"].get("artwork") or b"",
                  song["audio"] or b"", transcode, cover_name)


def find_package(pack: str, extra_dirs: list) -> Path | None:
    for d in extra_dirs:
        p = d / PACKAGES[pack]
        if p.exists():
            return p
    return None


def extract_pack(pack: str, pkg: Path, out_dir: Path, decrypt: Decryptor,
                 limit: int | None, jobs: int, mcz: bool, transcode: bool,
                 want_audio: bool) -> list:
    songs_dir = out_dir / pack
    songs_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg) as z:
        if len(z.infolist()) > MAX_ARCHIVE_ENTRIES:
            raise ChartError("备份条目数超过上限")
        names = sorted(n for n in z.namelist() if "/Documents/" in "/" + n and n.endswith(".jbt"))
    if not names:
        raise ChartError("备份中没有 Documents/*.jbt")
    if limit is not None:
        names = names[:limit]
    ids = [safe_name(Path(name).stem).casefold() for name in names]
    if len(ids) != len(set(ids)):
        raise ChartError("备份包含重复或大小写冲突的曲目 ID")
    manifest: list = []
    errors: list = []
    state = {"done": 0}
    total = len(names)

    def work(name: str):
        tune_id = safe_name(Path(name).stem)
        try:
            with zipfile.ZipFile(pkg) as z:
                blob = bounded_read(z, name)
            song = process_song(blob, tune_id, decrypt)
            if not want_audio:
                song["audio"] = None
                song["preview"] = None
            song_dir = songs_dir / ("%s_%s" % (tune_id, safe_name(song["title"])))
            if song_dir.exists():
                raise FileExistsError("输出曲目目录已存在；请使用新的输出目录")
            dump_song(song, song_dir, mcz, transcode)
        except Exception as exc:                          # noqa: BLE001
            errors.append((name, "%s: %s" % (type(exc).__name__, exc)))
            return None
        return {"id": tune_id, "title": song["title"], "artist": song["artist"],
                "yomi": song["yomi"], "dir": song_dir.name, "stats": song["stats"],
                "info": song["info"]}

    def track(res):
        state["done"] += 1
        if res:
            manifest.append(res)
        if state["done"] % 50 == 0 or state["done"] == total:
            print("  [%s] %d/%d" % (pack, state["done"], total), flush=True)
        return res

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        list(pool.map(lambda n: track(work(n)), names))
    if errors:
        print("  [%s] %d 首失败：" % (pack, len(errors)), file=sys.stderr)
        for name, msg in errors[:10]:
            print("    %s -> %s" % (name, msg), file=sys.stderr)
        raise ChartError("提取失败 %d 首；已完成的文件保留，不能视为全量成功" % len(errors))
    manifest.sort(key=lambda s: s["id"])
    return manifest


def extract_scimg(pkg: Path, out_dir: Path, decrypt: Decryptor) -> int:
    """plus 包里 Documents/scImg/*.img 是明文 JPEG，直接导出。"""
    out = out_dir / "scImg"
    n = 0
    with zipfile.ZipFile(pkg) as z:
        for name in z.namelist():
            if "/scImg/" not in name or not name.endswith(".img"):
                continue
            data = bounded_read(z, name)
            if data[:2] != b"\xff\xd8":
                try:
                    data = decrypt(data)
                except DecryptError:
                    continue
            if data[:2] != b"\xff\xd8":
                continue
            out.mkdir(parents=True, exist_ok=True)
            (out / (safe_name(Path(name).stem) + ".jpg")).write_bytes(data)
            n += 1
    return n


def build_report(manifests: dict, library_root: Path | None) -> dict:
    """产出对比报告（与现有曲库按「曲名」比对）。"""
    report: dict = {"counts": {k: len(v) for k, v in manifests.items()}}
    mobile: dict[str, dict] = {}
    for pack, items in manifests.items():
        for s in items:
            mobile.setdefault(s["title"], {"pack": pack, **s})
    report["mobile_titles"] = len(mobile)
    if not (library_root and library_root.exists()):
        return report
    lib: dict[str, dict] = {}
    for mcz in library_root.rglob("*.mcz"):
        try:
            with zipfile.ZipFile(mcz) as z:
                mcs = [n for n in z.namelist() if n.endswith(".mc")]
                if not mcs:
                    continue
                meta = json.loads(z.read(mcs[0]))["meta"]
                title = str(meta.get("song", {}).get("title", ""))
                if not title:
                    continue
                entry = lib.setdefault(title, {"version": mcz.parent.name, "levels": {}})
                entry["levels"][meta["version"]] = meta.get("level")
        except Exception:                                 # noqa: BLE001
            continue
    report["library_titles"] = len(lib)
    code_key = {"BSC": "bas", "ADV": "adv", "EXT": "ext"}
    new = []
    matched = []
    for title, s in sorted(mobile.items()):
        if title not in lib:
            new.append({"pack": s["pack"], "id": s["id"], "title": title,
                        "artist": s["artist"], "stats": s["stats"]})
            continue
        diffs = {}
        for version, lib_level in lib[title]["levels"].items():
            key = code_key.get(version.split()[0])
            if key and key in s["stats"]:
                diffs[version] = {"mobile_notes": s["stats"][key]["notes"],
                                  "mobile_level": s["stats"][key]["level"],
                                  "lib_level": lib_level}
        matched.append({"title": title, "pack": s["pack"], "id": s["id"], "diff": diffs})
    report["new_in_mobile"] = new
    report["new_count"] = len(new)
    report["matched"] = matched
    report["only_in_library"] = sorted(set(lib) - set(mobile))
    report["only_in_library_count"] = len(report["only_in_library"])
    return report


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="jubeat 移动版备份 → 歌曲/谱面/封面")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--packages", nargs="*", default=[],
                    help="直接指定 .adbk 路径（可多个）")
    ap.add_argument("--package-dir", action="append", default=[],
                    help="从该目录按固定文件名找 .adbk")
    ap.add_argument("--pack", nargs="*", choices=list(PACKAGES),
                    help="只处理指定包（默认全部）")
    ap.add_argument("--limit", type=int, help="每包只处理前 N 首（调试用）")
    ap.add_argument("--jobs", type=int, default=4, help="并发线程数（默认 4）")
    ap.add_argument("--mcz", action="store_true", help="额外产出曲库格式 .mcz")
    ap.add_argument("--transcode", action="store_true",
                    help="--mcz 时把音频转 ogg（与曲库一致，需要 ffmpeg）")
    ap.add_argument("--no-audio", action="store_true", help="不导出音频")
    ap.add_argument("--library", default=None, help="现有曲库 music/ 目录，用于对比")
    ap.add_argument("--scimg", action="store_true", help="额外导出 plus 的 scImg JPEG")
    args = ap.parse_args(argv)
    if args.jobs < 1 or args.jobs > 32 or (args.limit is not None and args.limit < 1):
        ap.error("--jobs 范围为 1..32，--limit 必须大于 0")
    if not args.packages and not args.package_dir:
        ap.error("请显式提供 --packages 或 --package-dir；不会扫描个人目录")
    if args.transcode and not args.mcz:
        ap.error("--transcode 需要同时指定 --mcz")
    out_dir = Path(args.out).expanduser().resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        ap.error("输出目录必须为空，避免覆盖旧提取结果")
    out_dir.mkdir(parents=True, exist_ok=True)
    decrypt = Decryptor()
    pkg_dirs = [Path(d).expanduser() for d in args.package_dir]
    packs = args.pack or list(PACKAGES)
    manifests: dict = {}
    missing = 0
    for pack in packs:
        pkg = None
        for cand in args.packages:
            p = Path(cand).expanduser()
            if p.exists() and (p.name == PACKAGES[pack] or p.stem == pack):
                pkg = p
                break
        if pkg is None:
            pkg = find_package(pack, pkg_dirs)
        if pkg is None:
            print("跳过 %s：找不到 %s" % (pack, PACKAGES[pack]), file=sys.stderr)
            missing += 1
            continue
        print("→ %s (%s)" % (pack, pkg), flush=True)
        manifests[pack] = extract_pack(pack, pkg, out_dir, decrypt, args.limit,
                                       args.jobs, args.mcz, args.transcode,
                                       not args.no_audio)
        if args.scimg:
            n = extract_scimg(pkg, out_dir, decrypt)
            if n:
                print("  [%s] scImg 导出 %d 张" % (pack, n))
    (out_dir / "library.json").write_text(
        json.dumps(manifests, ensure_ascii=False, indent=2), encoding="utf-8")
    library_root = Path(args.library).expanduser() if args.library else None
    report = build_report(manifests, library_root)
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n完成：")
    for pack, items in manifests.items():
        print("  %s: %d 首" % (pack, len(items)))
    if "new_count" in report:
        print("  曲库已有 %d 首；移动包新增 %d 首" %
              (report.get("library_titles", 0), report["new_count"]))
    return 1 if not manifests or (args.pack and missing) else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ChartError, DecryptError, OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print("提取失败：" + str(exc), file=sys.stderr)
        raise SystemExit(1)
