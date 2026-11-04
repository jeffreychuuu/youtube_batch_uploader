import shutil
import tempfile
import unittest
from pathlib import Path

from youtube_uploader.ffmpeg import (
    build_filter_graph, conversion_fingerprint, move_converted_to_source,
    verify_video, video_encode_args,
)


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


class VideoEncodeArgsTests(unittest.TestCase):
    def test_default_uses_libx264(self):
        self.assertEqual(video_encode_args(), [
            '-c:v', 'libx264', '-preset', 'medium', '-crf', '20',
        ])

    def test_videotoolbox_uses_quality(self):
        self.assertEqual(
            video_encode_args('h264_videotoolbox', 65),
            ['-c:v', 'h264_videotoolbox', '-q:v', '65'],
        )

    def test_libx264_uses_custom_crf(self):
        self.assertEqual(
            video_encode_args('libx264', 23)[-2:],
            ['-crf', '23'],
        )


class ConversionFingerprintTests(unittest.TestCase):
    def test_cuts_change_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'a.mp4'
            path.write_bytes(b'video')
            base = conversion_fingerprint([path])
            self.assertNotEqual(base, conversion_fingerprint([path], [(1, 2)]))

    def test_same_settings_produce_the_same_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'a.mp4'
            path.write_bytes(b'video')
            self.assertEqual(
                conversion_fingerprint([path], [(1, 2)]),
                conversion_fingerprint([path], [(1, 2)]),
            )

    def test_encoder_changes_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'a.mp4'
            path.write_bytes(b'video')
            libx264 = conversion_fingerprint([path], [(1, 2)])
            videotoolbox = conversion_fingerprint(
                [path], [(1, 2)], encoder='h264_videotoolbox', quality=65
            )
            self.assertNotEqual(libx264, videotoolbox)


class MoveAndVerifyTests(unittest.TestCase):
    def test_move_refuses_existing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            converted = Path(tmp) / 'converted.mp4'
            target = Path(tmp) / 'target.mp4'
            converted.write_bytes(b'a')
            target.write_bytes(b'b')
            with self.assertRaises(FileExistsError):
                move_converted_to_source(converted, target)

    @unittest.skipUnless(shutil.which('ffprobe'), 'ffprobe not installed')
    def test_verify_rejects_non_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'not-video.mp4'
            path.write_bytes(b'not a video')
            with self.assertRaises(RuntimeError):
                verify_video(path)


if __name__ == '__main__':
    unittest.main()
