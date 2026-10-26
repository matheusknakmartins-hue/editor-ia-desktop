import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

import assembly_cut


def word(start, end):
    return {'start': start, 'end': end, 'confidence': 0.95, 'text': 'teste'}


class EndpointTests(unittest.TestCase):
    def test_zero_duration_word_and_unordered_alignment(self):
        end, _ = assembly_cut.endpoint(
            {'words': [word(500, 900), word(100, 300), word(1000, 1000)]}, 2)
        self.assertEqual(end, 1)

    def test_small_rounding_overrun_is_clamped(self):
        end, _ = assembly_cut.endpoint({'words': [word(500, 2030)]}, 2)
        self.assertEqual(end, 2)

    def test_invalid_times_still_fail(self):
        for start, end in [(-1, 100), (200, 100), (0, float('nan')),
                           (False, 100), (0, 2200), (0, 0)]:
            with self.subTest(start=start, end=end), self.assertRaises(RuntimeError):
                assembly_cut.endpoint({'words': [word(start, end)]}, 2)

    def test_low_confidence_still_fails(self):
        last = word(500, 1000)
        last['confidence'] = 0.2
        with self.assertRaisesRegex(RuntimeError, 'baixa confiança'):
            assembly_cut.endpoint({'words': [last]}, 2)

    def test_extracted_audio_matches_renderable_duration(self):
        def run(command):
            return subprocess.run(command, check=True, capture_output=True)

        def transcribe(record, data, duration, key):
            with wave.open(io.BytesIO(data)) as audio:
                self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), 2, places=2)
            return 1.5, {}

        with tempfile.TemporaryDirectory() as folder:
            clip = Path(folder) / 'long-audio.mp4'
            run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                 'color=s=32x32:d=2', '-f', 'lavfi', '-i',
                 'sine=duration=3', '-c:v', 'libx264', '-threads', '1',
                 '-c:a', 'aac', str(clip)])
            with patch.object(assembly_cut, '_transcribe_record', side_effect=transcribe):
                end, _ = assembly_cut.transcribe_end(
                    clip, 2, 'test-key', Path(folder) / 'cache', 'ffmpeg', run)
            self.assertEqual(end, 1.5)


if __name__ == '__main__':
    unittest.main()
