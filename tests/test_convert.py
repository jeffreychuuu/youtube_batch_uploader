import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_uploader.convert import run_convert_only

BASE_CONFIG = {
    'root_folder': None,
    'video_extensions': ['.mp4', '.vob'],
    'convert_extensions': ['.vob'],
    'export_converted_source': True,
    'remove_original_after_conversion': False,
    'remove_original_permanent': False,
}


class ConvertOnlyTests(unittest.TestCase):
    def test_requires_export_converted_source(self):
        config = dict(BASE_CONFIG, export_converted_source=False)
        with self.assertRaises(SystemExit):
            run_convert_only(config)

    @patch('youtube_uploader.convert.save_state')
    @patch('youtube_uploader.convert.prepare_source')
    @patch('youtube_uploader.convert.load_state',
           return_value={'uploads': {}, 'golden_sources': {}})
    def test_converts_only_convert_jobs(self, load_state, prepare, save):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Folder'
            folder.mkdir()
            (folder / 'VTS_01_1.VOB').touch()
            (folder / 'VTS_01_2.VOB').touch()
            (Path(tmp) / 'Clip.mp4').touch()
            summary = run_convert_only(dict(BASE_CONFIG, root_folder=tmp))
        self.assertEqual(summary, {'converted': 1, 'skipped': 1, 'failed': 0})
        self.assertEqual(prepare.call_count, 1)
        job = prepare.call_args.args[0]
        self.assertTrue(job['convert'])
        self.assertEqual(job['title'], 'Folder')

    @patch('youtube_uploader.convert.save_state')
    @patch('youtube_uploader.convert.prepare_source')
    @patch('youtube_uploader.convert.load_state')
    def test_skips_jobs_with_an_existing_golden(self, load_state, prepare, save):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Folder'
            folder.mkdir()
            (folder / 'VTS_01_1.VOB').touch()
            (folder / 'VTS_01_2.VOB').touch()
            golden = folder / 'Folder.mp4'
            golden.touch()
            load_state.return_value = {
                'uploads': {},
                'golden_sources': {
                    str(golden.resolve()): {'title': 'Folder'}
                },
            }
            summary = run_convert_only(dict(BASE_CONFIG, root_folder=tmp))
        self.assertEqual(summary, {'converted': 0, 'skipped': 1, 'failed': 0})
        prepare.assert_not_called()

    @patch('youtube_uploader.convert.save_state')
    @patch('youtube_uploader.convert.prepare_source',
           side_effect=RuntimeError('boom'))
    @patch('youtube_uploader.convert.load_state',
           return_value={'uploads': {}, 'golden_sources': {}})
    def test_counts_failures_and_continues(self, load_state, prepare, save):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('A', 'B'):
                folder = Path(tmp) / name
                folder.mkdir()
                (folder / 'VTS_01_1.VOB').touch()
                (folder / 'VTS_01_2.VOB').touch()
            summary = run_convert_only(dict(BASE_CONFIG, root_folder=tmp))
        self.assertEqual(summary, {'converted': 0, 'skipped': 0, 'failed': 2})
        self.assertEqual(prepare.call_count, 2)


if __name__ == '__main__':
    unittest.main()
