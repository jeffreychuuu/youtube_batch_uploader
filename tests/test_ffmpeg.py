import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_uploader.ffmpeg import (
    build_filter_graph, conversion_fingerprint, copy_fingerprint,
    move_converted_to_source, remove_copy_cache, split_without_encode,
    verify_video,
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


class CopySplitTests(unittest.TestCase):
    def test_fingerprint_changes_with_part_hours(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'a.mp4'
            source.write_bytes(b'video')
            self.assertNotEqual(
                copy_fingerprint(source, 1),
                copy_fingerprint(source, 0.25),
            )

    def test_move_refuses_existing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            converted = Path(tmp) / 'converted.mp4'
            target = Path(tmp) / 'target.mp4'
            converted.write_bytes(b'a')
            target.write_bytes(b'b')
            with self.assertRaises(FileExistsError):
                move_converted_to_source(converted, target)

    @staticmethod
    def _make_clip(path, keyframe_frames=None):
        args = [
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-f', 'lavfi', '-i', 'testsrc=duration=5:size=320x240:rate=10',
            '-f', 'lavfi', '-i', 'sine=frequency=440:duration=5',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac',
        ]
        if keyframe_frames:
            args.extend(['-g', str(keyframe_frames)])
        args.append(str(path))
        subprocess.run(args, check=True)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg not installed')
    def test_split_without_encode_produces_parts(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            'youtube_uploader.ffmpeg.CONVERTED_DIR',
            Path(tmp) / '.converted',
        ):
            source = Path(tmp) / 'clip.mp4'
            self._make_clip(source, keyframe_frames=10)
            verify_video(source)
            try:
                parts = split_without_encode(source, 0.001)
                self.assertGreaterEqual(len(parts), 2)
                self.assertTrue(all(part.exists() for part in parts))
            finally:
                remove_copy_cache(source, 0.001)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg not installed')
    def test_split_without_encode_rejects_sparse_keyframes(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            'youtube_uploader.ffmpeg.CONVERTED_DIR',
            Path(tmp) / '.converted',
        ):
            source = Path(tmp) / 'clip.mp4'
            self._make_clip(source)
            try:
                with self.assertRaises(RuntimeError):
                    split_without_encode(source, 0.001)
            finally:
                remove_copy_cache(source, 0.001)

    @unittest.skipUnless(shutil.which('ffprobe'), 'ffprobe not installed')
    def test_verify_rejects_non_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'not-video.mp4'
            path.write_bytes(b'not a video')
            with self.assertRaises(RuntimeError):
                verify_video(path)


if __name__ == '__main__':
    unittest.main()
