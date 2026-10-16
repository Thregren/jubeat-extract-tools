#!/usr/bin/env python3
"""jubeat 街机 / PC 游戏资源中的 IFS → .mcz 转换工具。

数据来源（`--extract` 指向解包根目录）
-------------------------------------
    data/ifs_pack/d{mid 前 7 位}/{mid}_msc.ifs

IFS 是"分块包"，共 5 块，固定顺序：

    blk0 = BGM（Konami BMP / OKI4S ADPCM）
    blk1 = 试听音频（可选导出；时长由实际数据决定）
    blk2 = EXT 谱面    blk3 = ADV 谱面    blk4 = BSC 谱面

区块表在文件 0x10 处的 BE32 偏移减 60 处，每条 12 字节（BE32 偏移 + BE32 长度）。

谱面算法
--------
300 Hz tick 经 TEMPO 时间轴转换为拍值，再按 1/48 拍取整。
适配范围与历史验证见 docs/arcade-format.md 和 docs/validation.md。

音源算法
--------
Konami BMP 不是 PCM，是 OKI4S ADPCM（等价于 vgmstream 的 `coding_OKI4S`）：
每半字节一个采样，step 表 49 项（下标 0..48），步长索引变化表 16 项；
双声道时一字节高 4 位是左声道、低 4 位是右声道。默认保持解码电平再编码
Vorbis；只有显式指定 --target-rms-db 才做 RMS 归一化。手算见 docs/arcade-format.md。

用法
----
    python3 arcade/import_ifs.py --songs input/songs.json \
        --extract input/contents --out output/arcade --charts-only
"""
from __future__ import annotations

import argparse
import array
import bisect
import io
import json
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
import wave
import zipfile
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path

MAX_IFS_BYTES = 128 * 1024 * 1024

# ---------------------------------------------------------------- 谱面转换

# IFS 里难度块的固定下标（见模块开头）
_BLOCK = {"EXT": 2, "ADV": 3, "BSC": 4}
# jubeat 内部 tick 频率：300 Hz
_TICK_HZ = 300
# 拍值取整粒度：1/48 拍（与曲库既有谱面一致）
_BEAT_SNAP = 48
# 从长押起点到终点的坐标增量；index = x + 4*y，不以箭头朝向命名。
_DIR_VEC = {0: (0, -1), 1: (0, 1), 2: (-1, 0), 3: (1, 0)}


class ChartError(ValueError):
    """谱面无法解析，例如结构越界、事件无效或疑似占位块。"""


def load_ifs(path: Path) -> tuple[bytes, list[tuple[int, int]]]:
    """读 IFS 分块表：返回 (整包字节, [(块偏移, 块长度) × 5])。"""
    if path.stat().st_size > MAX_IFS_BYTES:
        raise ChartError("IFS 超出 128 MiB 上限")
    data = path.read_bytes()
    if len(data) < 0x14:
        raise ChartError(f"IFS 太小：{path}")
    table_off = struct.unpack_from(">I", data, 0x10)[0]
    start = table_off - 60
    if start < 0 or start + 12 * 5 > len(data):
        raise ChartError(f"IFS 分块表越界：{path}")
    entries = []
    for i in range(5):
        off, size = struct.unpack_from(">II", data, start + 12 * i)
        if table_off + off + size > len(data):
            raise ChartError("IFS 分块范围超出文件")
        entries.append((table_off + off, size))
    return data, entries


def read_block(ifs: Path, index: int) -> bytes:
    data, entries = load_ifs(ifs)
    off, size = entries[index]
    if off + size > len(data):
        raise ChartError(f"IFS 块 {index} 越界：{ifs}")
    return data[off:off + size]


def _parse_events(text: bytes) -> list[tuple[int, str, int]]:
    """谱面明文（cp932）→ [(tick, 指令, 值)]。"""
    out: list[tuple[int, str, int]] = []
    for line in text.decode("cp932", "replace").splitlines():
        if not line.strip():
            continue
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 2 or not parts[1]:
            continue
        try:
            pos = int(parts[0])
        except ValueError:
            continue
        if parts[1] in ("MEASURE", "END", "HAKU"):
            out.append((pos, parts[1], 0))
            continue
        try:
            value = int(parts[2])
        except (ValueError, IndexError):
            continue
        out.append((pos, parts[1], value))
    return out


def _truncate(value: Fraction, places: int) -> Fraction:
    scale = Fraction(10) ** places
    return Fraction(math.floor(value * scale), scale)


def _value_to_bpm(value: int) -> Fraction:
    """从截断整数回推可兼容的短小数 BPM；截断信息不能唯一恢复。"""
    if value <= 0:
        raise ChartError("TEMPO 必须大于 0")
    exact = Fraction(6 * 10 ** 7, value)
    places = 0
    while places <= 8:
        candidate = _truncate(exact, places)
        if candidate > 0 and 6 * 10 ** 7 / candidate < value + 1:
            return candidate
        places += 1
    return exact


def _round_beats(beats: Fraction, denom: int = _BEAT_SNAP) -> Fraction:
    return Fraction(round(beats * denom), denom)


class _TimeMap:
    """BPM 变化点（按秒）→ 拍轴。谱面里的 note 位置只有 tick，拍值要靠它积分。"""

    def __init__(self, points: list[tuple[Fraction, Fraction]]):
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

    def seconds_at(self, beat: Fraction) -> Fraction:
        sec, base, bpm = self.changes[0]
        return sec + (beat - base) * 60 / bpm


def parse_chart(ifs: Path, code: str) -> tuple[list, list, Fraction]:
    """解一个难度。

    返回 (notes, timing, offset)：
        notes  = [(beat, index, endbeat|None, endindex|None)]
        timing = [(beat, bpm)]，即 Malody `time` 数组
        offset = 第 0 拍的秒数（本项目的谱面不带偏移，恒为 0）
    """
    block = _BLOCK[code]
    try:
        raw = read_block(ifs, block)
    except ChartError:
        raise
    if len(raw) < 32:
        raise ChartError(f"{ifs.name} 的 {code} 块是占位（官方未配信）")

    events = _parse_events(raw)
    # TEMPO is stateful: the last record in source order wins at one tick.
    # Sorting (tick, value) reverses some simultaneous changes and can make
    # every later note drift. Earlier changes at that tick have zero duration.
    tempo_by_tick = {}
    for tick, cmd, value in events:
        if cmd == "TEMPO":
            tempo_by_tick[tick] = value
    tempos = sorted(tempo_by_tick.items())
    if not tempos:
        raise ChartError(f"{ifs.name} 的 {code} 块里没有 TEMPO")
    if tempos[0][0] != 0:
        raise ChartError(f"{ifs.name} 的 {code} 块 TEMPO 不从 tick 0 开始")

    tm = _TimeMap([(Fraction(t, _TICK_HZ), _value_to_bpm(v)) for t, v in tempos])

    notes = []
    for tick, cmd, value in events:
        if cmd == "PLAY":
            if tick < 0 or not 0 <= value < 16:
                raise ChartError("单点时间或位置无效")
            notes.append((_round_beats(tm.beats_at(Fraction(tick, _TICK_HZ))),
                          value, None, None))
        elif cmd == "LONG":
            if tick < 0 or value < 0:
                raise ChartError("长押时间或编码无效")
            index = value & 15
            direction = (value >> 4) & 3
            length = (value >> 6) & 3
            duration = value >> 8
            raw_beat = tm.beats_at(Fraction(tick, _TICK_HZ))
            beat = _round_beats(raw_beat)
            endbeat = _round_beats(
                tm.beats_at(Fraction(tick + duration, _TICK_HZ)) - raw_beat)
            dx, dy = _DIR_VEC[direction]
            x, y = index % 4, index // 4
            endx, endy = x + dx * length, y + dy * length
            notes.append((beat, index, beat + endbeat, endx + 4 * endy))

    # 极短长押经拍值取整后可能头尾同拍，仍保留长押，避免改变物量口径。
    notes.sort(key=lambda n: (n[0], n[1]))
    timing = [(_round_beats(beat), bpm) for _, beat, bpm in tm.changes]
    return notes, timing, tm.seconds_at(Fraction(0))


def beat_tuple(beat: Fraction) -> list[int]:
    """Fraction → Malody 的 [整数拍, 分子, 分母]。"""
    whole = int(beat)
    frac = beat - whole
    if frac == 0:
        return [whole, 0, 1]
    return [whole, frac.numerator, frac.denominator]


# ---------------------------------------------------------------- 音源解码

# OKI4S 的步长表（49 项，下标 0..48）与步长索引变化表
_STEP_SIZES = (
    16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66,
    73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253,
    279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876,
    963, 1060, 1166, 1282, 1411, 1552,
)
_STEP_DELTA = (-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8)


class AudioError(ValueError):
    """音源不是可解的 Konami BMP。"""


def parse_bmp_header(data: bytes) -> dict:
    if len(data) < 0x20 or data[:4] != b"BMP\x00":
        raise AudioError("不是 Konami BMP（缺少 BMP\\0 魔数）")
    num_samples, loop_start, loop_end = struct.unpack_from(">III", data, 4)
    channels = data[0x10] or struct.unpack_from("<H", data, 16)[0]
    sample_rate = struct.unpack_from(">I", data, 0x14)[0]
    if channels not in (1, 2):
        raise AudioError(f"声道数不支持：{channels}")
    if not sample_rate:
        raise AudioError("采样率为 0")
    adpcm = data[0x20:]
    limit = len(adpcm) * (2 if channels == 1 else 1)
    if num_samples == 0 or num_samples > limit:
        num_samples = limit
    return {
        "num_samples": num_samples, "channels": channels,
        "sample_rate": sample_rate, "loop_start": loop_start,
        "loop_end": loop_end, "adpcm": adpcm,
    }


def decode_bmp_to_pcm(data: bytes) -> tuple[bytes, int, int]:
    """Konami BMP → (16-bit LE PCM, 采样率, 声道数)，保持单/双声道。"""
    head = parse_bmp_header(data)
    adpcm = head["adpcm"]
    channels = head["channels"]
    count = head["num_samples"]
    out = bytearray(count * channels * 2)
    hist = [0, 0]
    step = [0, 0]

    for i in range(count):
        if channels == 2:
            byte = adpcm[i] if i < len(adpcm) else 0
            nibbles = ((byte >> 4) & 0xF, byte & 0xF)
        else:
            byte = adpcm[i // 2] if (i // 2) < len(adpcm) else 0
            nibbles = ((byte >> 4) & 0xF,) if (i & 1) == 0 else (byte & 0xF,)
        for ch, code in enumerate(nibbles):
            delta = _STEP_SIZES[step[ch]] << 4
            acc = delta >> 3
            if code & 1:
                acc += delta >> 2
            if code & 2:
                acc += delta >> 1
            if code & 4:
                acc += delta
            if code & 8:
                acc = -acc
            hist[ch] = max(-32768, min(32767, hist[ch] + acc))
            step[ch] = max(0, min(len(_STEP_SIZES) - 1, step[ch] + _STEP_DELTA[code]))
            struct.pack_into("<h", out, (i * channels + ch) * 2, hist[ch])

    return bytes(out), head["sample_rate"], channels


# ---------------------------------------------------------------- 响度归一

# 可选归一化的历史默认目标，单位 dBFS；CLI 可调整。
TARGET_RMS_DB = -14.61
# Vorbis 编解码会掉一点电平，编码时先补回来
_VORBIS_OFFSET_DB = 0.45
# Vorbis 编码质量参数。
VORBIS_QUALITY = 5


def _pcm_rms_db(pcm: bytes) -> float:
    if not pcm:
        return float("-inf")
    total = 0
    count = len(pcm) // 2
    for i in range(count):
        sample = int.from_bytes(pcm[i * 2:i * 2 + 2], "little", signed=True)
        total += sample * sample
    if total <= 0:
        return float("-inf")
    return 20.0 * math.log10(math.sqrt(total / count) / 32768.0)


def _amplify_pcm(pcm: bytes, gain: float) -> bytes:
    """按倍率放大并限幅（16-bit PCM）。"""
    if not pcm or abs(gain - 1.0) < 1e-9:
        return pcm
    samples = array.array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    for i, value in enumerate(samples):
        scaled = int(value * gain)
        samples[i] = -32768 if scaled < -32768 else 32767 if scaled > 32767 else scaled
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


def _pcm_to_wav(pcm: bytes, rate: int, channels: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def _oggenc() -> str | None:
    """本机有没有 libvorbis 编码器（oggenc 优先，其次 ffmpeg 自带 libvorbis）。"""
    from shutil import which
    return which("oggenc")


def _ffmpeg(*args: str, data: bytes | None = None) -> bytes:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error",
                           "-nostdin", *args],
                          input=data, capture_output=True, timeout=900)
    if proc.returncode != 0:
        raise AudioError(proc.stderr.decode("utf-8", "replace").strip()[:400])
    return proc.stdout


def encode_vorbis(wav: bytes) -> bytes:
    """WAV → Ogg Vorbis（libvorbis）。

    优先用 oggenc；否则使用 ffmpeg 原生 vorbis 编码器。
    ffmpeg 是否包含 libvorbis 取决于其构建选项。
    """
    exe = _oggenc()
    if exe:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.wav"
            dst = Path(tmp) / "out.ogg"
            src.write_bytes(wav)
            proc = subprocess.run([exe, "-Q", "-q", str(VORBIS_QUALITY),
                                   "-o", str(dst), str(src)],
                                  capture_output=True, timeout=900)
            if proc.returncode == 0 and dst.is_file():
                return dst.read_bytes()
            raise AudioError(proc.stderr.decode("utf-8", "replace").strip()[:200]
                             or "oggenc 编码失败")
    return _ffmpeg("-i", "pipe:0", "-c:a", "vorbis", "-strict", "-2",
                   "-q:a", str(VORBIS_QUALITY), "-f", "ogg", "pipe:1", data=wav)


def bmp_to_ogg(data: bytes, target_rms_db: float | None = None) -> bytes:
    """Konami BMP → Ogg Vorbis；仅指定目标 RMS 时调整电平。"""
    pcm, rate, channels = decode_bmp_to_pcm(data)
    if target_rms_db is None:
        return encode_vorbis(_pcm_to_wav(pcm, rate, channels))
    rms = _pcm_rms_db(pcm)
    if math.isfinite(rms):
        gain_db = (target_rms_db + _VORBIS_OFFSET_DB) - rms
    else:
        gain_db = 0.0
    ogg = encode_vorbis(_pcm_to_wav(_amplify_pcm(pcm, 10 ** (gain_db / 20)),
                                    rate, channels))

    # 编出来再量一次，偏得多就补一次（有损编码后电平总归会飘一点）
    decoded = _ffmpeg("-i", "pipe:0", "-f", "s16le", "-acodec", "pcm_s16le",
                      "pipe:1", data=ogg)
    after = _pcm_rms_db(decoded)
    if math.isfinite(after):
        correction = target_rms_db - after
        if abs(correction) > 0.15:
            ogg = encode_vorbis(_pcm_to_wav(
                _amplify_pcm(pcm, 10 ** ((gain_db + correction) / 20)),
                rate, channels))
    return ogg


# ---------------------------------------------------------------- 打包 .mcz

_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(text: str) -> str:
    """曲名 → 文件名（去掉文件系统不友好的字符）。"""
    cleaned = _ILLEGAL.sub("_", unicodedata.normalize("NFC", text)).strip().rstrip(".")
    while len(cleaned.encode("utf-8")) > 160:
        cleaned = cleaned[:-1]
    return cleaned or "untitled"


@contextmanager
def atomic_zip(out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".mcz-", suffix=".tmp", dir=out_path.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            yield archive
        # Exclusive publication: an existing file is never overwritten.
        os.link(temp, out_path)
    finally:
        Path(temp).unlink(missing_ok=True)


def build_mcz(
    *,
    ifs: Path,
    cover: bytes,
    mid: str,
    title: str,
    artist: str,
    levels: dict[str, str],
    out_path: Path,
    charts_only: bool = False,
    target_rms_db: float | None = None,
    with_preview: bool = False,
) -> dict:
    """按曲库既有格式写一个 .mcz，返回统计信息。"""
    if charts_only and with_preview:
        raise ValueError("--charts-only 与 --with-preview 不能同时使用")
    audio = b"" if charts_only else bmp_to_ogg(read_block(ifs, 0), target_rms_db)
    # Use the original preview block; do not manufacture a clip from the BGM.
    # Empty blocks are allowed, but malformed nonempty audio must fail.
    preview_raw = read_block(ifs, 1) if with_preview else b""
    preview = bmp_to_ogg(preview_raw, target_rms_db) if preview_raw else b""
    cover_name = f"jkt_{mid}.png" if cover else ""
    stamp = int(time.time())
    stats = {}

    with atomic_zip(out_path) as z:
        for code in ("BSC", "ADV", "EXT"):
            notes, timing, _ = parse_chart(ifs, code)
            level = str(levels[code])
            version = f"{code} Lv{level}"
            chart = {
                "meta": {
                    "background": cover_name, "version": version, "id": 0,
                    "mode": 4, "time": stamp,
                    "song": {"title": title, "artist": artist, "id": 0},
                    "mode_ext": {}, "cover": cover_name,
                    "level": level, "creator": "jubeat",
                },
                "time": [{"beat": beat_tuple(beat), "bpm": float(bpm)}
                         for beat, bpm in timing],
                "note": ([{"beat": [0, 0, 1], "sound": "bgm.ogg",
                           "type": 1, "offset": 0, "vol": 100}] if audio else []),
                "extra": {version: {"divide": 4, "speed": 100, "save": 0,
                                    "lock": 0, "edit_mode": 0}},
            }
            for beat, index, endbeat, endindex in notes:
                note = {"beat": beat_tuple(beat), "index": index}
                if endbeat is not None:
                    note["endbeat"] = beat_tuple(endbeat)
                    note["endindex"] = endindex
                chart["note"].append(note)

            stats[code] = {
                "notes": len(notes),
                "holds": sum(1 for n in notes if n[2] is not None),
                "bpm": round(float(timing[0][1]), 4),
            }
            z.writestr(f"0/{safe_name(title)}_{version}.mc",
                       json.dumps(chart, ensure_ascii=False, separators=(",", ":")))
        if audio:
            z.writestr("0/bgm.ogg", audio)
        if preview:
            z.writestr("0/preview.ogg", preview)
        if cover:
            z.writestr(f"0/{cover_name}", cover)

    stats["audio_bytes"] = len(audio)
    stats["preview_bytes"] = len(preview)
    stats["preview_status"] = ("exported" if preview else "absent") if with_preview else "not_requested"
    stats["mcz_bytes"] = out_path.stat().st_size
    return stats


# ---------------------------------------------------------------- 命令行

def load_song_list(path: Path) -> list[dict]:
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("曲目清单超过 8 MiB 上限")
    songs = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(songs, list) or not 1 <= len(songs) <= 20_000:
        raise ValueError("曲目清单必须为包含 1..20000 项的数组")
    seen = set()
    for song in songs:
        if not isinstance(song, dict):
            raise ValueError("每项曲目信息必须是对象")
        mid = str(song.get("mid", ""))
        version = song.get("version", "")
        if not re.fullmatch(r"[0-9]{8}", mid):
            raise ValueError("mid 必须为 8 位十进制字符串")
        if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", version):
            raise ValueError("version 目录名只允许字母、数字、下划线和连字符")
        if not isinstance(song.get("title"), str) or not song["title"].strip():
            raise ValueError("title 必须是非空字符串")
        if not isinstance(song.get("artist"), str):
            raise ValueError("artist 必须是字符串")
        levels = song.get("levels", {})
        if not isinstance(levels, dict):
            raise ValueError("levels 必须是对象")
        for code in _BLOCK:
            try:
                level = float(levels[code])
                if not math.isfinite(level) or not 0 <= level <= 20:
                    raise ValueError()
            except (KeyError, ValueError, TypeError):
                raise ValueError("levels 必须包含有效的 BSC / ADV / EXT 等级") from None
        identity = (version.casefold(), mid)
        if identity in seen:
            raise ValueError("同一 version 中有重复 mid")
        seen.add(identity)
        song["mid"] = mid
    return songs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="jubeat IFS → .mcz")
    ap.add_argument("--songs", required=True, help="自备曲目元数据 JSON")
    ap.add_argument("--extract", required=True, help="输入根目录（含 data/ifs_pack）")
    ap.add_argument("--covers", help="可选封面目录（{mid}.png）")
    ap.add_argument("--out", required=True, help="输出根目录")
    ap.add_argument("--only", help="只处理这些 mid（逗号分隔）")
    ap.add_argument("--dry-run", action="store_true", help="检查输入，不写文件")
    ap.add_argument("--charts-only", action="store_true", help="只转换谱面，无需音频编码器")
    ap.add_argument("--with-preview", action="store_true", help="将原始试听块转为 0/preview.ogg；空块跳过")
    ap.add_argument("--target-rms-db", type=float, help="可选响度归一目标；默认保持解码电平")
    args = ap.parse_args(argv)
    if args.charts_only and args.with_preview:
        ap.error("--charts-only 与 --with-preview 不能同时使用")
    if args.target_rms_db is not None and (not math.isfinite(args.target_rms_db) or not -60 <= args.target_rms_db <= -1):
        ap.error("--target-rms-db 必须在 -60..-1 之间")
    songs = load_song_list(Path(args.songs).expanduser())
    only = set(args.only.split(",")) if args.only else None
    if only and not only <= {song["mid"] for song in songs}:
        ap.error("--only 包含清单中不存在的 mid")
    pack_dir = Path(args.extract).expanduser() / "data" / "ifs_pack"
    cover_dir = Path(args.covers).expanduser() if args.covers else None
    out_root = Path(args.out).expanduser().resolve()
    done = failed = skipped = 0
    for song in songs:
        mid = song["mid"]
        if only and mid not in only:
            continue
        ifs = pack_dir / f"d{mid[:7]}" / f"{mid}_msc.ifs"
        cover_path = cover_dir / f"{mid}.png" if cover_dir else None
        out_path = out_root / song["version"] / f"{safe_name(song['title'])}__{mid}.mcz"
        try:
            if not out_path.resolve().is_relative_to(out_root):
                raise ValueError("输出路径超出指定目录")
            if not ifs.is_file():
                raise FileNotFoundError(f"找不到 IFS：{mid}")
            if cover_path and not cover_path.is_file():
                raise FileNotFoundError(f"缺少封面：{mid}")
            if out_path.exists():
                print(f"[skip] {mid} 目标已存在，保持原文件")
                skipped += 1
                continue
            if args.dry_run:
                for code in _BLOCK:
                    parse_chart(ifs, code)
                print(f"[dry ] {mid} → {out_path.relative_to(out_root)}")
                continue
            stats = build_mcz(
                ifs=ifs, cover=cover_path.read_bytes() if cover_path else b"",
                mid=mid, title=song["title"], artist=song["artist"],
                levels=song["levels"], out_path=out_path,
                charts_only=args.charts_only, target_rms_db=args.target_rms_db,
                with_preview=args.with_preview,
            )
        except (ChartError, AudioError, OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f"[fail] {mid}: {exc}", file=sys.stderr)
            failed += 1
            continue
        done += 1
        print(f"[ ok ] {mid} " + "  ".join(
            f"{code}:{stats[code]['notes']}n/{stats[code]['holds']}h"
            for code in ("BSC", "ADV", "EXT")))
    print(f"完成 {done} 首，已有跳过 {skipped} 首，失败 {failed} 首")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print("导入失败：" + str(exc), file=sys.stderr)
        raise SystemExit(1)
