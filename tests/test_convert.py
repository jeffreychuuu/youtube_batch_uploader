import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_uploader.convert import run_convert_only

BASE_CONFIG = {
    'root_folder': None,
    'convert_extensions': ['.vob'],
    'combine': True,
    'remove_original_after_conversion': False,
    'remove_original_permanent': False,
}


class ConvertOnlyTests(unittest.TestCase):
    @patch('youtube_uploader.convert.save_convert_state')
    @patch('youtube_uploader.convert.prepare_source')
    @patch('youtube_uploader.convert.load_convert_state',
           return_value={})
    def test_converts_only_convert_extensions(self, load_state, prepare, save):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Folder'
            folder.mkdir()
            (folder / 'VTS_01_1.VOB').touch()
            (folder / 'VTS_01_2.VOB').touch()
            (Path(tmp) / 'Clip.mp4').touch()
            summary = run_convert_only(dict(BASE_CONFIG, root_folder=tmp))
        self.assertEqual(summary, {'converted': 1, 'skipped': 0, 'failed': 0})
        self.assertEqual(prepare.call_count, 1)
        job = prepare.call_args.args[0]
        self.assertTrue(job['convert'])
        self.assertEqual(job['title'], 'Folder [combined]')

    @patch('youtube_uploader.convert.save_convert_state')
    @patch('youtube_uploader.convert.prepare_source')
    @patch('youtube_uploader.convert.load_convert_state')
    def test_skips_jobs_with_an_existing_golden(self, load_state, prepare, save):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Folder'
            folder.mkdir()
            (folder / 'VTS_01_1.VOB').touch()
            (folder / 'VTS_01_2.VOB').touch()
            golden = folder / 'Folder [combined].mp4'
            golden.touch()
            load_state.return_value = {
                str(golden.resolve()): {
                    'title': 'Folder',
                    'source_files': [
                        str((folder / 'VTS_01_1.VOB').resolve()),
                        str((folder / 'VTS_01_2.VOB').resolve()),
                    ],
                },
            }
            summary = run_convert_only(dict(BASE_CONFIG, root_folder=tmp))
        self.assertEqual(summary, {'converted': 0, 'skipped': 1, 'failed': 0})
        prepare.assert_not_called()

    @patch('youtube_uploader.convert.save_convert_state')
    @patch('youtube_uploader.convert.load_convert_state')
    def test_removes_originals_for_already_converted_jobs(
        self, load_state, save
    ):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Folder'
            folder.mkdir()
            sources = [folder / 'VTS_01_1.VOB', folder / 'VTS_01_2.VOB']
            for source in sources:
                source.touch()
            golden = folder / 'Folder [combined].mp4'
            golden.touch()
            load_state.return_value = {
                str(golden.resolve()): {
                    'title': 'Folder',
                    'source_files': [
                        str(source.resolve()) for source in sources
                    ],
                },
            }
            config = dict(
                BASE_CONFIG,
                root_folder=tmp,
                remove_original_after_conversion=True,
                remove_original_permanent=True,
            )
            summary = run_convert_only(config)
            self.assertTrue(golden.exists())
            self.assertFalse(any(source.exists() for source in sources))
        self.assertEqual(summary, {'converted': 0, 'skipped': 1, 'failed': 0})

    @patch('youtube_uploader.convert.save_convert_state')
    @patch('youtube_uploader.convert.prepare_source',
           side_effect=RuntimeError('boom'))
    @patch('youtube_uploader.convert.load_convert_state',
           return_value={})
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
