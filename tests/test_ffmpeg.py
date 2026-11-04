import tempfile
import unittest
from pathlib import Path

from youtube_uploader.ffmpeg import build_filter_graph, conversion_fingerprint


class BuildFilterGraphTests(unittest.TestCase):
    def test_single_input_with_audio(self):
        graph, map_args = build_filter_graph(
            [Path('a.vob')], [], has_audio=True,
            fps_expr='25/1', sample_rate=48000,
        )
        self.assertIn('[0:v:0]setpts=N/((25/1)*TB)[nv0]', graph)
        self.assertIn('[0:a:0]asetpts=N/(48000*TB)[na0]', graph)
        self.assertIn('[nv0]yadif=deint=interlaced[vout]', graph)
        self.assertEqual(map_args, ['-map', '[vout]', '-map', '[na0]'])

    def test_multiple_inputs_are_concatenated(self):
        graph, _ = build_filter_graph(
            [Path('a.vob'), Path('b.vob')], [], has_audio=False,
            fps_expr='25/1', sample_rate=None,
        )
        self.assertIn('[nv0][nv1]concat=n=2:v=1:a=0[sv]', graph)
        self.assertNotIn('asetpts', graph)

    def test_cuts_trim_and_concatenate_remaining_blocks(self):
        graph, map_args = build_filter_graph(
            [Path('a.vob')], [(10, 20), (30, 40)], has_audio=False,
            fps_expr='25/1', sample_rate=None,
        )
        self.assertIn('trim=start=0:end=10', graph)
        self.assertIn('trim=start=20:end=30', graph)
        self.assertIn('trim=start=40', graph)
        self.assertIn('concat=n=3:v=1:a=0[vcat]', graph)
        self.assertEqual(map_args, ['-map', '[vout]'])

    def test_cuts_with_audio_trim_both_streams(self):
        graph, map_args = build_filter_graph(
            [Path('a.vob')], [(10, 20)], has_audio=True,
            fps_expr='25/1', sample_rate=48000,
        )
        self.assertIn('atrim=start=0:end=10', graph)
        self.assertIn('atrim=start=20', graph)
        self.assertEqual(map_args, ['-map', '[vout]', '-map', '[acat]'])


class ConversionFingerprintTests(unittest.TestCase):
    def test_part_length_and_cuts_change_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'a.mp4'
            path.write_bytes(b'video')
            base = conversion_fingerprint([path], 1)
            self.assertNotEqual(base, conversion_fingerprint([path], 2))
            self.assertNotEqual(base, conversion_fingerprint([path], 1, [(1, 2)]))

    def test_same_settings_produce_the_same_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'a.mp4'
            path.write_bytes(b'video')
            self.assertEqual(
                conversion_fingerprint([path], 1, [(1, 2)]),
                conversion_fingerprint([path], 1, [(1, 2)]),
            )


if __name__ == '__main__':
    unittest.main()
