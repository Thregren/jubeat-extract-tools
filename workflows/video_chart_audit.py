"""Audit a video-transcribed chart against a standard MIDI reference.

This tool reports evidence; it does not rewrite the source transcription.
No third-party packages or game assets are required.
"""
from __future__ import annotations

import argparse
import json
import statistics
from bisect import bisect_right
from pathlib import Path


def _variable(data: bytes, pos: int) -> tuple[int, int]:
    number = 0
    while True:
        byte = data[pos]
        pos += 1
        number = (number << 7) | (byte & 127)
        if byte < 128:
            return number, pos


def read_midi(path: Path) -> dict:
    data = path.read_bytes()
    if data[:4] != b"MThd" or int.from_bytes(data[4:8], "big") != 6:
        raise ValueError("not a standard MIDI file")
    if int.from_bytes(data[8:10], "big") != 1:
        raise ValueError("expected type-1 MIDI")
    count = int.from_bytes(data[10:12], "big")
    resolution = int.from_bytes(data[12:14], "big")
    if resolution & 0x8000:
        raise ValueError("SMPTE timing is unsupported")
    pos = 14
    tracks = {}
    changes = {0: 500000}
    for _ in range(count):
        if data[pos:pos + 4] != b"MTrk":
            raise ValueError("missing MIDI track")
        length = int.from_bytes(data[pos + 4:pos + 8], "big")
        raw = data[pos + 8:pos + 8 + length]
        pos += 8 + length
        at = tick = 0
        running = None
        name = ""
        notes = []
        chart_events = []
        while at < len(raw):
            delta, at = _variable(raw, at)
            tick += delta
            status = raw[at]
            if status >= 0x80:
                at += 1
                if status < 0xF0:
                    running = status
            else:
                if running is None:
                    raise ValueError("running MIDI status without prior channel message")
                status = running
            if status == 0xFF:
                kind = raw[at]
                at += 1
                size, at = _variable(raw, at)
                value = raw[at:at + size]
                at += size
                if kind == 3:
                    name = value.decode("utf-8", "replace").rstrip("\0")
                if kind == 0x51 and size == 3:
                    changes[tick] = int.from_bytes(value, "big")
            elif status in (0xF0, 0xF7):
                size, at = _variable(raw, at)
                at += size
            elif 0x80 <= status <= 0xEF:
                kind = status & 0xF0
                size = 1 if kind in (0xC0, 0xD0) else 2
                value = raw[at:at + size]
                at += size
                if len(value) != size:
                    raise ValueError("truncated MIDI message")
                if kind == 0x90 and value[1] > 0:
                    notes.append((tick, value[0]))
                    chart_events.append((tick, status, tuple(value)))
                elif kind == 0x80 or kind == 0x90 and value[1] == 0:
                    chart_events.append((tick, status, tuple(value)))
            else:
                raise ValueError(f"unsupported MIDI status {status:02x}")
        tracks[name] = {"notes": notes, "chart_events": chart_events}
    if pos != len(data):
        raise ValueError("unexpected trailing MIDI bytes")
    tempos = sorted(changes.items())
    ticks = [tick for tick, _ in tempos]
    seconds = [0.0]
    for i in range(1, len(tempos)):
        seconds.append(seconds[-1] + (tempos[i][0] - tempos[i - 1][0]) * tempos[i - 1][1] / (resolution * 1_000_000))

    def second(tick: int) -> float:
        i = bisect_right(ticks, tick) - 1
        return seconds[i] + (tick - ticks[i]) * tempos[i][1] / (resolution * 1_000_000)

    return {"resolution": resolution, "tracks": tracks, "second": second}


def audit(reference: dict, events: dict, difficulty: str = "ext", pitch_base: int = 36,
          audio_offset_seconds: float | None = None) -> dict:
    tracks = reference["tracks"]
    if difficulty not in tracks:
        raise ValueError(f"missing MIDI track {difficulty}")
    if events.get("tick_hz", 0) <= 0:
        raise ValueError("video events require a positive tick_hz")
    native = sorted((reference["second"](tick), pitch - pitch_base)
                    for tick, pitch in tracks[difficulty]["notes"])
    if any(not 0 <= panel < 16 for _, panel in native):
        raise ValueError("MIDI pitch cannot map into a 4x4 panel")
    video = sorted((event["tick"] / events["tick_hz"], int(event["value"]))
                   for event in events["events"] if event["command"] in ("PLAY", "LONG"))
    if any(not 0 <= panel < 16 for _, panel in video):
        raise ValueError("video panel outside the 4x4 grid")
    wrong = [{"ordinal": i + 1, "midi_seconds": round(a[0], 6),
              "reference_panel": a[1], "video_panel": b[1]}
             for i, (a, b) in enumerate(zip(native, video)) if a[1] != b[1]]
    shifts = [b[0] - a[0] for a, b in zip(native, video)]
    offset = statistics.median(shifts) if shifts else None
    residuals = sorted(abs(shift - offset) for shift in shifts) if shifts else []
    signatures = {name: tuple(track["chart_events"]) for name, track in tracks.items()
                  if name in ("bsc", "adv", "ext")}
    cloned = [name for name in ("bsc", "adv") if name in signatures and signatures[name] == signatures.get("ext")]
    return {"reference_notes": len(native), "video_notes": len(video),
            "ordered_panel_mismatches": len(wrong), "wrong_panels": wrong,
            "median_video_minus_midi_seconds": round(offset, 6) if offset is not None else None,
            "video_audio_minus_reference_audio_seconds": audio_offset_seconds,
            "median_after_audio_alignment_seconds": round(offset - audio_offset_seconds, 6)
            if offset is not None and audio_offset_seconds is not None else None,
            "absolute_timing_residual_p95_seconds": round(residuals[round((len(residuals) - 1) * .95)], 6) if residuals else None,
            "absolute_timing_residual_max_seconds": round(residuals[-1], 6) if residuals else None,
            "cloned_lower_difficulty_tracks": cloned,
            "comparison_limit": "Ordinal matching can misalign nearby notes when counts differ or event order crosses; review video frames before correction."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--midi", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--difficulty", default="ext", choices=("bsc", "adv", "ext"))
    parser.add_argument("--pitch-base", type=int, default=36)
    parser.add_argument("--audio-offset-seconds", type=float,
                        help="Measured video-audio minus reference-audio lag; positive means video audio is later")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = audit(read_midi(args.midi), json.loads(args.events.read_text()), args.difficulty,
                   args.pitch_base, args.audio_offset_seconds)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
