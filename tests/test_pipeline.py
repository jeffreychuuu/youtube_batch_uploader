import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_uploader.pipeline import (
    SourcePlan, cleanup_plan_cache, media_parts_for_plan, prepare_source,
    should_remove_sources,
)


class PrepareSourceTests(unittest.TestCase):
    def test_without_export_returns_convert_pipeline(self):
        job = {'title': 'Folder', 'files': [Path('/a/VTS_01_1.VOB')], 'convert': True}
        plan = prepare_source(job, {'uploads': {}, 'golden_sources': {}}, {
            'export_converted_source': False,
        })
        self.assertEqual(plan.mode, 'convert_pipeline')

    def test_non_convert_returns_single_source(self):
        job = {'title': 'Clip', 'files': [Path('/a/Clip.mp4')], 'convert': False}
        plan = prepare_source(job, {'uploads': {}, 'golden_sources': {}}, {
            'export_converted_source': True,
        })
        self.assertEqual(plan.mode, 'single')
        self.assertEqual(plan.files, [Path('/a/Clip.mp4')])

    @patch('youtube_uploader.pipeline.save_state')
    @patch('youtube_uploader.pipeline.move_converted_to_source')
    @patch('youtube_uploader.pipeline.verify_video')
    @patch('youtube_uploader.pipeline.convert_to_mp4',
           return_value=[Path('/cache/x.mp4')])
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
            state = {'uploads': {}, 'golden_sources': {}}
            plan = prepare_source(job, state, {
                'export_converted_source': True,
                'remove_original_after_conversion': False,
            })
        self.assertEqual(plan.mode, 'single')
        self.assertTrue(plan.golden)
        self.assertEqual(plan.key, str(target.resolve()))
        convert.assert_called_once_with(sources, 0, [])
        self.assertIn(str(target.resolve()), state['golden_sources'])
        save.assert_called_once_with(state)

    @patch('youtube_uploader.pipeline.save_state')
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
                'uploads': {},
                'golden_sources': {str(target.resolve()): {'title': 'Folder'}},
            }
            plan = prepare_source(job, state, {
                'export_converted_source': True,
                'remove_original_after_conversion': False,
            })
        self.assertTrue(plan.golden)
        convert.assert_not_called()

    @patch('youtube_uploader.pipeline.save_state')
    @patch('youtube_uploader.pipeline.remove_local_file')
    @patch('youtube_uploader.pipeline.move_converted_to_source')
    @patch('youtube_uploader.pipeline.verify_video')
    @patch('youtube_uploader.pipeline.convert_to_mp4',
           return_value=[Path('/cache/x.mp4')])
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
            prepare_source(job, {'uploads': {}, 'golden_sources': {}}, {
                'export_converted_source': True,
                'remove_original_after_conversion': True,
                'remove_original_permanent': True,
            })
        self.assertEqual(remove.call_count, 2)
        remove.assert_any_call(sources[0], True)


class MediaPartsTests(unittest.TestCase):
    @patch('youtube_uploader.pipeline.convert_to_mp4',
           return_value=[Path('/out/p1.mp4')])
    def test_convert_pipeline_uses_convert_to_mp4(self, convert):
        plan = SourcePlan([Path('/a.VOB')], '/a.VOB', 'convert_pipeline', False)
        media_parts_for_plan(plan, 1, [(1, 2)])
        convert.assert_called_once_with(plan.files, 1, [(1, 2)])

    @patch('youtube_uploader.pipeline.convert_to_mp4',
           return_value=[Path('/out/p1.mp4')])
    def test_single_with_cuts_reencodes(self, convert):
        plan = SourcePlan([Path('/a.mp4')], '/a.mp4', 'single', True)
        media_parts_for_plan(plan, 1, [(1, 2)])
        convert.assert_called_once_with(plan.files, 1, [(1, 2)])

    @patch('youtube_uploader.pipeline.split_without_encode',
           return_value=[Path('/out/p1.mp4')])
    def test_single_without_cuts_splits_with_copy(self, split):
        plan = SourcePlan([Path('/a.mp4')], '/a.mp4', 'single', True)
        media_parts_for_plan(plan, 1, [])
        split.assert_called_once_with(Path('/a.mp4'), 1)

    @patch('youtube_uploader.pipeline.convert_to_mp4',
           return_value=[Path('/out/p1.mp4')])
    @patch('youtube_uploader.pipeline.split_without_encode',
           side_effect=RuntimeError('no'))
    def test_copy_split_failure_falls_back_to_reencode(self, split, convert):
        plan = SourcePlan([Path('/a.mp4')], '/a.mp4', 'single', True)
        parts = media_parts_for_plan(plan, 1, [])
        self.assertEqual(parts, [Path('/out/p1.mp4')])
        convert.assert_called_once_with(plan.files, 1, [])

    @patch('youtube_uploader.pipeline.convert_to_mp4')
    @patch('youtube_uploader.pipeline.split_without_encode')
    def test_single_without_cuts_and_no_split_uploads_as_is(self, split, convert):
        plan = SourcePlan([Path('/a.mp4')], '/a.mp4', 'single', True)
        parts = media_parts_for_plan(plan, 0, [])
        self.assertEqual(parts, [Path('/a.mp4')])
        split.assert_not_called()
        convert.assert_not_called()


class CleanupAndRemovalTests(unittest.TestCase):
    @patch('youtube_uploader.pipeline.remove_cached_conversion')
    def test_convert_pipeline_removes_conversion_cache(self, remove):
        plan = SourcePlan([Path('/a.VOB')], '/a.VOB', 'convert_pipeline', False)
        cleanup_plan_cache(plan, 1, [(1, 2)])
        remove.assert_called_once_with(plan.files, 1, [(1, 2)])

    @patch('youtube_uploader.pipeline.remove_copy_cache')
    def test_single_copy_split_removes_copy_cache(self, remove):
        plan = SourcePlan([Path('/a.mp4')], '/a.mp4', 'single', True)
        cleanup_plan_cache(plan, 1, [])
        remove.assert_called_once_with(Path('/a.mp4'), 1)

    def test_should_remove_sources_never_removes_golden(self):
        plan = SourcePlan([Path('/a.mp4')], '/a.mp4', 'single', True)
        self.assertFalse(
            should_remove_sources(plan, {'remove_local_after_upload': True})
        )

    def test_should_remove_sources_for_non_golden_single(self):
        plan = SourcePlan([Path('/a.mp4')], '/a.mp4', 'single', False)
        self.assertTrue(
            should_remove_sources(plan, {'remove_local_after_upload': True})
        )

    def test_should_remove_sources_for_convert_pipeline(self):
        plan = SourcePlan([Path('/a.VOB')], '/a.VOB', 'convert_pipeline', False)
        self.assertTrue(
            should_remove_sources(plan, {'remove_local_after_upload': True})
        )


if __name__ == '__main__':
    unittest.main()
