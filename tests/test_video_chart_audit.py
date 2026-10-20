"""Synthetic-only checks for video/MIDI calibration."""
import tempfile
import unittest
from pathlib import Path

from workflows.video_chart_audit import audit, read_midi


def variable(number):
    parts = [number & 127]
    number >>= 7
    while number:
        parts.insert(0, 128 | (number & 127))
        number >>= 7
    return bytes(parts)


def track(name, notes):
    content = b"\x00\xff\x03" + variable(len(name)) + name
    at = 0
    for tick, pitch in notes:
        content += variable(tick - at) + bytes((0x90, pitch, 100))
        at = tick
    content += b"\x00\xff\x2f\x00"
    return b"MTrk" + len(content).to_bytes(4, "big") + content


class VideoChartAuditTests(unittest.TestCase):
    def test_tick_zero_tempo_panel_swap_and_cloned_tracks(self):
        tempo = b"\x00\xff\x51\x03\x06\x1a\x80\x00\xff\x2f\x00"  # 400000 us/beat
        header = b"MThd\x00\x00\x00\x06\x00\x01\x00\x04\x01\xe0"
        midi = (header + b"MTrk" + len(tempo).to_bytes(4, "big") + tempo
                + track(b"bsc", [(480, 36), (960, 37)])
                + track(b"adv", [(480, 36), (960, 37)])
                + track(b"ext", [(480, 36), (960, 37)]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.mid"
            path.write_bytes(midi)
            reference = read_midi(path)
        self.assertAlmostEqual(reference["second"](480), 0.4)
        events = {"tick_hz": 1000, "events": [
            {"tick": 450, "command": "PLAY", "value": 0},
            {"tick": 850, "command": "PLAY", "value": 2},
        ]}
        result = audit(reference, events, audio_offset_seconds=0.05)
        self.assertEqual(result["reference_notes"], 2)
        self.assertEqual(result["ordered_panel_mismatches"], 1)
        self.assertEqual(result["cloned_lower_difficulty_tracks"], ["bsc", "adv"])
        self.assertAlmostEqual(result["median_video_minus_midi_seconds"], 0.05)
        self.assertEqual(result["median_after_audio_alignment_seconds"], 0)
        self.assertEqual(result["absolute_timing_residual_p95_seconds"], 0)

    def test_invalid_panel_is_rejected(self):
        reference = {"tracks": {"ext": {"notes": [(0, 60)], "chart_events": []}},
                     "second": lambda tick: 0.0}
        with self.assertRaisesRegex(ValueError, "4x4"):
            audit(reference, {"tick_hz": 1000, "events": []})


if __name__ == "__main__":
    unittest.main()
