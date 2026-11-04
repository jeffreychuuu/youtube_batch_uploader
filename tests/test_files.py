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
            self.assertEqual(jobs[0]['title'], 'My Video [combined]')
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


class CombineModeTests(unittest.TestCase):
    def test_none_mode_keeps_vob_segments_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Folder'
            folder.mkdir()
            files = [folder / 'VTS_01_1.VOB', folder / 'VTS_01_2.VOB']
            for path in files:
                path.touch()

            jobs = build_upload_jobs(files, {'.vob'}, combine_mode='none')

            self.assertEqual(
                [job['title'] for job in jobs], ['VTS_01_1', 'VTS_01_2']
            )
            self.assertTrue(all(job['convert'] for job in jobs))

    def test_folder_mode_groups_same_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Folder'
            folder.mkdir()
            files = [folder / 'b.m2ts', folder / 'a.m2ts']
            for path in files:
                path.touch()

            jobs = build_upload_jobs(
                files, {'.m2ts'},
                combine_mode='folder', combine_extensions=['.m2ts'],
            )

            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]['title'], 'Folder [combined]')
            self.assertEqual(
                jobs[0]['files'], [folder / 'a.m2ts', folder / 'b.m2ts']
            )

    def test_folder_mode_keeps_vob_title_sets(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Folder'
            folder.mkdir()
            files = [folder / 'VTS_01_1.VOB', folder / 'VTS_01_2.VOB']
            for path in files:
                path.touch()

            jobs = build_upload_jobs(
                files, {'.vob'},
                combine_mode='folder', combine_extensions=['.vob'],
            )

            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]['title'], 'Folder [combined]')

    def test_vob_mode_ignores_other_extensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Folder'
            folder.mkdir()
            files = [folder / 'a.m2ts', folder / 'b.m2ts']
            for path in files:
                path.touch()

            jobs = build_upload_jobs(
                files, {'.m2ts'},
                combine_mode='vob', combine_extensions=['.m2ts'],
            )

            self.assertEqual(len(jobs), 2)

    def test_folder_mode_disambiguates_extension_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Folder'
            folder.mkdir()
            files = [
                folder / 'a.m2ts', folder / 'b.m2ts',
                folder / 'c.avi', folder / 'd.avi',
            ]
            for path in files:
                path.touch()

            jobs = build_upload_jobs(
                files, {'.m2ts', '.avi'},
                combine_mode='folder',
                combine_extensions=['.m2ts', '.avi'],
            )

            self.assertEqual(
                sorted(job['title'] for job in jobs),
                ['Folder (AVI) [combined]', 'Folder (M2TS) [combined]'],
            )

    def test_folder_mode_ignores_unlisted_extensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Folder'
            folder.mkdir()
            files = [folder / 'a.m2ts', folder / 'b.m2ts']
            for path in files:
                path.touch()

            jobs = build_upload_jobs(
                files, {'.m2ts'},
                combine_mode='folder', combine_extensions=['.vob'],
            )

            self.assertEqual(len(jobs), 2)


    def test_custom_combined_name_overrides_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Folder'
            folder.mkdir()
            files = [folder / 'VTS_01_1.VOB', folder / 'VTS_01_2.VOB']
            for path in files:
                path.touch()

            jobs = build_upload_jobs(
                files, {'.vob'}, combined_names={'Folder': 'My Custom'},
            )

            self.assertEqual(jobs[0]['title'], 'My Custom')

    def test_custom_name_uses_group_suffix_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Folder'
            folder.mkdir()
            files = [
                folder / 'a.m2ts', folder / 'b.m2ts',
                folder / 'c.avi', folder / 'd.avi',
            ]
            for path in files:
                path.touch()

            jobs = build_upload_jobs(
                files, {'.m2ts', '.avi'},
                combine_mode='folder',
                combine_extensions=['.m2ts', '.avi'],
                combined_names={'Folder (M2TS)': 'Custom M2TS'},
            )

            titles = sorted(job['title'] for job in jobs)
            self.assertEqual(titles, ['Custom M2TS', 'Folder (AVI) [combined]'])


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
