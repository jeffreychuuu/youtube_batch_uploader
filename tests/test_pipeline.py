import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_uploader.ffmpeg import remove_cached_conversion, verify_video
from youtube_uploader.pipeline import (
    SourcePlan, cleanup_plan_cache, media_parts_for_plan, prepare_source,
    remove_original_sources, should_remove_sources,
)


class PrepareSourceTests(unittest.TestCase):
    def test_non_convert_returns_single_source(self):
        job = {'title': 'Clip', 'files': [Path('/a/Clip.mp4')], 'convert': False}
        plan = prepare_source(job, {}, {})
        self.assertFalse(plan.golden)
        self.assertEqual(plan.files, [Path('/a/Clip.mp4')])

    @patch('youtube_uploader.pipeline.save_convert_state')
    @patch('youtube_uploader.pipeline.move_converted_to_source')
    @patch('youtube_uploader.pipeline.verify_video')
    @patch('youtube_uploader.pipeline.convert_to_mp4',
           return_value=Path('/cache/x.mp4'))
    @patch('youtube_uploader.pipeline.converted_source_path')
    def test_export_converts_records_and_switches_key(
        self, path_mock, convert, verify, move, save
    ):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'Folder.mp4'
            path_mock.return_value = target
            sources = [Path(tmp) / 'VTS_01_1.VOB']
            job = {
                'title': 'Folder',
                'files': sources,
                'convert': True,
            }
            state = {}
            plan = prepare_source(job, state, {
                'remove_original_after_conversion': False,
            })
        self.assertTrue(plan.golden)
        self.assertEqual(plan.key, str(target.resolve()))
        convert.assert_called_once_with(
            sources, encoder='libx264', quality=20,
        )
        self.assertIn(str(target.resolve()), state)
        save.assert_called_once_with(state)

    @patch('youtube_uploader.pipeline.save_convert_state')
    @patch('youtube_uploader.pipeline.move_converted_to_source')
    @patch('youtube_uploader.pipeline.verify_video')
    @patch('youtube_uploader.pipeline.convert_to_mp4',
           return_value=Path('/cache/x.mp4'))
    @patch('youtube_uploader.pipeline.converted_source_path')
    def test_export_passes_encoder_settings(
        self, path_mock, convert, verify, move, save
    ):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'Folder.mp4'
            path_mock.return_value = target
            sources = [Path(tmp) / 'VTS_01_1.VOB']
            job = {'title': 'Folder', 'files': sources, 'convert': True}
            prepare_source(job, {}, {
                'convert_encoder': 'h264_videotoolbox',
                'convert_quality': 65,
                'remove_original_after_conversion': False,
            })
        convert.assert_called_once_with(
            sources, encoder='h264_videotoolbox', quality=65,
        )

    @patch('youtube_uploader.pipeline.save_convert_state')
    @patch('youtube_uploader.pipeline.verify_video')
    @patch('youtube_uploader.pipeline.convert_to_mp4')
    @patch('youtube_uploader.pipeline.converted_source_path')
    def test_existing_recorded_golden_is_reused(
        self, path_mock, convert, verify, save
    ):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'Folder.mp4'
            target.touch()
            path_mock.return_value = target
            job = {
                'title': 'Folder',
                'files': [Path(tmp) / 'VTS_01_1.VOB'],
                'convert': True,
            }
            state = {
                    str(target.resolve()): {
                        'title': 'Folder',
                        'source_files': [str(job['files'][0].resolve())],
                    }
            }
            plan = prepare_source(job, state, {
                'remove_original_after_conversion': False,
            })
        self.assertTrue(plan.golden)
        convert.assert_not_called()

    @patch('youtube_uploader.pipeline.save_convert_state')
    @patch('youtube_uploader.pipeline.verify_video')
    @patch('youtube_uploader.pipeline.convert_to_mp4')
    @patch('youtube_uploader.pipeline.converted_source_path')
    def test_reuse_allows_subset_of_recorded_sources(
        self, path_mock, convert, verify, save
    ):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'Folder.mp4'
            target.touch()
            kept = Path(tmp) / 'VTS_01_3.VOB'
            removed = Path(tmp) / 'VTS_01_1.VOB'
            path_mock.return_value = target
            job = {'title': 'Folder', 'files': [kept], 'convert': True}
            state = {
                    str(target.resolve()): {
                        'title': 'Folder',
                        'source_files': [
                            str(removed.resolve()), str(kept.resolve())
                        ],
                    }
            }
            plan = prepare_source(job, state, {
                'remove_original_after_conversion': False,
            })
        self.assertTrue(plan.golden)
        convert.assert_not_called()

    @patch('youtube_uploader.pipeline.save_convert_state')
    @patch('youtube_uploader.pipeline.verify_video')
    @patch('youtube_uploader.pipeline.convert_to_mp4')
    @patch('youtube_uploader.pipeline.converted_source_path')
    def test_reuse_refuses_provenance_mismatch(
        self, path_mock, convert, verify, save
    ):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'Folder.mp4'
            target.touch()
            other = Path(tmp) / 'Other.VOB'
            other.touch()
            path_mock.return_value = target
            job = {
                'title': 'Folder',
                'files': [Path(tmp) / 'Folder.VOB'],
                'convert': True,
            }
            state = {
                    str(target.resolve()): {
                        'title': 'Folder',
                        'source_files': [str(other.resolve())],
                    }
            }
            with self.assertRaises(SystemExit):
                prepare_source(job, state, {
                    'remove_original_after_conversion': False,
                })
        convert.assert_not_called()

    @patch('youtube_uploader.pipeline.save_convert_state')
    @patch('youtube_uploader.pipeline.convert_to_mp4')
    @patch('youtube_uploader.pipeline.converted_source_path')
    def test_unrecorded_existing_target_stops(self, path_mock, convert, save):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'Folder.mp4'
            target.touch()
            path_mock.return_value = target
            job = {
                'title': 'Folder',
                'files': [Path(tmp) / 'Folder.VOB'],
                'convert': True,
            }
            with self.assertRaises(SystemExit):
                prepare_source(job, {}, {
                    'remove_original_after_conversion': False,
                })
        convert.assert_not_called()

    @patch('youtube_uploader.pipeline.remove_cached_conversion')
    @patch('youtube_uploader.pipeline.save_convert_state')
    @patch('youtube_uploader.pipeline.verify_video',
           side_effect=RuntimeError('bad output'))
    @patch('youtube_uploader.pipeline.convert_to_mp4',
           return_value=[Path('/cache/x.mp4')])
    @patch('youtube_uploader.pipeline.converted_source_path')
    def test_verification_failure_clears_cache(
        self, path_mock, convert, verify, save, remove
    ):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'Folder.mp4'
            path_mock.return_value = target
            sources = [Path(tmp) / 'VTS_01_1.VOB', Path(tmp) / 'VTS_01_2.VOB']
            job = {'title': 'Folder', 'files': sources, 'convert': True}
            with self.assertRaises(RuntimeError):
                prepare_source(job, {}, {
                    'remove_original_after_conversion': False,
                })
        remove.assert_called_once_with(
            sources, encoder='libx264', quality=20,
        )

    @patch('youtube_uploader.pipeline.save_convert_state')
    @patch('youtube_uploader.pipeline.remove_local_file')
    @patch('youtube_uploader.pipeline.move_converted_to_source')
    @patch('youtube_uploader.pipeline.verify_video')
    @patch('youtube_uploader.pipeline.convert_to_mp4',
           return_value=Path('/cache/x.mp4'))
    @patch('youtube_uploader.pipeline.converted_source_path')
    def test_export_removes_originals_when_enabled(
        self, path_mock, convert, verify, move, remove, save
    ):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'Folder.mp4'
            path_mock.return_value = target
            sources = [Path(tmp) / 'VTS_01_1.VOB', Path(tmp) / 'VTS_01_2.VOB']
            for source in sources:
                source.touch()
            job = {'title': 'Folder', 'files': sources, 'convert': True}
            prepare_source(job, {}, {
                'remove_original_after_conversion': True,
                'remove_original_permanent': True,
            })
        self.assertEqual(remove.call_count, 2)
        remove.assert_any_call(sources[0], True)


class MediaPartsTests(unittest.TestCase):
    @patch('youtube_uploader.pipeline.convert_to_mp4',
           return_value=Path('/out/p1.mp4'))
    def test_cuts_reencode(self, convert):
        plan = SourcePlan([Path('/a.mp4')], '/a.mp4', True)
        media_parts_for_plan(plan, [(1, 2)])
        convert.assert_called_once_with(
            plan.files, [(1, 2)], encoder=None, quality=None,
        )

    @patch('youtube_uploader.pipeline.convert_to_mp4')
    def test_without_cuts_uploads_as_is(self, convert):
        plan = SourcePlan([Path('/a.mp4')], '/a.mp4', True)
        parts = media_parts_for_plan(plan, [])
        self.assertEqual(parts, [Path('/a.mp4')])
        convert.assert_not_called()

    @patch('youtube_uploader.pipeline.convert_to_mp4',
           return_value=Path('/out/p1.mp4'))
    def test_cuts_pass_encoder_settings(self, convert):
        plan = SourcePlan([Path('/a.mp4')], '/a.mp4', True)
        media_parts_for_plan(
            plan, [(1, 2)], 'h264_videotoolbox', 65
        )
        convert.assert_called_once_with(
            plan.files, [(1, 2)],
            encoder='h264_videotoolbox', quality=65,
        )


class CleanupAndRemovalTests(unittest.TestCase):
    @patch('youtube_uploader.pipeline.remove_cached_conversion')
    def test_cuts_remove_conversion_cache(self, remove):
        plan = SourcePlan([Path('/a.mp4')], '/a.mp4', True)
        cleanup_plan_cache(plan, [(1, 2)])
        remove.assert_called_once_with(
            plan.files, [(1, 2)], encoder=None, quality=None,
        )

    @patch('youtube_uploader.pipeline.remove_cached_conversion')
    def test_without_cuts_keeps_conversion_cache(self, remove):
        plan = SourcePlan([Path('/a.mp4')], '/a.mp4', True)
        cleanup_plan_cache(plan, [])
        remove.assert_not_called()

    @patch('youtube_uploader.pipeline.remove_cached_conversion')
    def test_cleanup_passes_encoder_settings(self, remove):
        plan = SourcePlan([Path('/a.mp4')], '/a.mp4', True)
        cleanup_plan_cache(
            plan, [(1, 2)], 'h264_videotoolbox', 65
        )
        remove.assert_called_once_with(
            plan.files, [(1, 2)],
            encoder='h264_videotoolbox', quality=65,
        )

    def test_should_remove_sources_never_removes_golden(self):
        plan = SourcePlan([Path('/a.mp4')], '/a.mp4', True)
        self.assertFalse(
            should_remove_sources(plan, {'remove_local_after_upload': True})
        )

    def test_should_remove_sources_for_non_golden_single(self):
        plan = SourcePlan([Path('/a.mp4')], '/a.mp4', False)
        self.assertTrue(
            should_remove_sources(plan, {'remove_local_after_upload': True})
        )


class RemoveOriginalSourcesTests(unittest.TestCase):
    @patch('youtube_uploader.pipeline.remove_local_file',
           side_effect=OSError('boom'))
    def test_removal_failure_is_only_a_warning(self, remove):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'a.vob'
            source.touch()
            remove_original_sources([source], {
                'remove_original_after_conversion': True,
                'remove_original_permanent': False,
            })
        remove.assert_called_once_with(source, False)

    @patch('youtube_uploader.pipeline.remove_local_file')
    def test_missing_source_is_ignored(self, remove):
        with tempfile.TemporaryDirectory() as tmp:
            remove_original_sources([Path(tmp) / 'gone.vob'], {
                'remove_original_after_conversion': True,
                'remove_original_permanent': False,
            })
        remove.assert_not_called()

    def test_keeps_empty_folder_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Root'
            folder = root / 'A'
            folder.mkdir(parents=True)
            source = folder / 'a.avi'
            source.touch()

            remove_original_sources([source], {
                'remove_original_after_conversion': True,
                'remove_original_permanent': True,
                'root_folder': root,
            })

            self.assertFalse(source.exists())
            self.assertTrue(folder.exists())

    def test_removes_empty_folder_and_root_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Root'
            folder = root / 'A'
            folder.mkdir(parents=True)
            source = folder / 'a.avi'
            source.touch()

            remove_original_sources([source], {
                'remove_original_after_conversion': True,
                'remove_original_permanent': True,
                'remove_empty_folders': True,
                'root_folder': root,
            })

            self.assertFalse(source.exists())
            self.assertFalse(folder.exists())
            self.assertFalse(root.exists())


class GoldenSmokeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg not installed')
    def test_real_conversion_creates_golden(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            'youtube_uploader.ffmpeg.CONVERTED_DIR',
            Path(tmp) / '.converted',
        ):
            source = Path(tmp) / 'Source.mkv'
            subprocess.run([
                'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                '-f', 'lavfi', '-i', 'testsrc=duration=2:size=320x240:rate=10',
                '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac',
                str(source),
            ], check=True)
            job = {'title': 'Source', 'files': [source], 'convert': True}
            state = {}
            config = {
                'remove_original_after_conversion': False,
                'remove_original_permanent': False,
            }
            try:
                with patch('youtube_uploader.pipeline.save_convert_state'):
                    plan = prepare_source(job, state, config)
                target = Path(tmp) / 'Source.mp4'
                self.assertTrue(target.exists())
                self.assertTrue(plan.golden)
                self.assertIn(str(target.resolve()), state)
                verify_video(target)
            finally:
                remove_cached_conversion([source])


if __name__ == '__main__':
    unittest.main()
