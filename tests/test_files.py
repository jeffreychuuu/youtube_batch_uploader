import tempfile
import unittest
from pathlib import Path

from youtube_uploader.files import (
    build_upload_jobs, converted_source_path, natural_sort_key,
)


class NaturalSortKeyTests(unittest.TestCase):
    def test_numbers_sort_numerically(self):
        names = ['10.mp4', '2.mp4', '1.mp4']
        self.assertEqual(
            sorted(names, key=natural_sort_key),
            ['1.mp4', '2.mp4', '10.mp4'],
        )


class BuildUploadJobsTests(unittest.TestCase):
    def test_merges_multi_segment_vob_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'My Video'
            folder.mkdir()
            files = [folder / 'VTS_01_1.VOB', folder / 'VTS_01_2.VOB']
            for path in files:
                path.touch()

            jobs = build_upload_jobs(files, {'.vob'})

            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]['files'], files)
            self.assertEqual(jobs[0]['title'], 'My Video')
            self.assertTrue(jobs[0]['convert'])

    def test_non_converted_files_are_single_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = folder / 'clip.mp4'
            path.touch()

            jobs = build_upload_jobs([path], {'.vob'})

            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]['files'], [path])
            self.assertEqual(jobs[0]['title'], 'clip')
            self.assertFalse(jobs[0]['convert'])

    def test_skips_dvd_menu_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            menu = folder / 'VTS_01_0.VOB'
            menu.touch()

            self.assertEqual(build_upload_jobs([menu], {'.vob'}), [])


class ConvertedSourcePathTests(unittest.TestCase):
    def test_merged_vob_uses_folder_title(self):
        job = {
            'title': 'Family',
            'files': [Path('/v/1995/VTS_01_1.VOB'), Path('/v/1995/VTS_01_2.VOB')],
        }
        self.assertEqual(converted_source_path(job), Path('/v/1995/Family.mp4'))

    def test_video_ts_uses_parent_folder(self):
        job = {'title': 'Family', 'files': [Path('/v/1995/VIDEO_TS/VTS_01_1.VOB')]}
        self.assertEqual(converted_source_path(job), Path('/v/1995/Family.mp4'))

    def test_single_file_uses_stem(self):
        job = {'title': 'Clip', 'files': [Path('/v/Clip.mp4')]}
        self.assertEqual(converted_source_path(job), Path('/v/Clip.mp4'))


if __name__ == '__main__':
    unittest.main()
