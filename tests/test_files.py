import tempfile
import unittest
from pathlib import Path

from youtube_uploader.files import (
    build_upload_jobs, converted_output_path, converted_source_path,
    natural_sort_key, remove_empty_folders,
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


class CombineTests(unittest.TestCase):
    def test_combine_false_keeps_vob_segments_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Folder'
            folder.mkdir()
            files = [folder / 'VTS_01_1.VOB', folder / 'VTS_01_2.VOB']
            for path in files:
                path.touch()

            jobs = build_upload_jobs(files, {'.vob'}, combine=False)

            self.assertEqual(
                [job['title'] for job in jobs], ['VTS_01_1', 'VTS_01_2']
            )
            self.assertTrue(all(job['convert'] for job in jobs))

    def test_combine_true_groups_same_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Folder'
            folder.mkdir()
            files = [folder / 'b.m2ts', folder / 'a.m2ts']
            for path in files:
                path.touch()

            jobs = build_upload_jobs(files, {'.m2ts'}, combine=True)

            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]['title'], 'Folder [combined]')
            self.assertEqual(
                jobs[0]['files'], [folder / 'a.m2ts', folder / 'b.m2ts']
            )

    def test_combine_true_keeps_vob_title_sets(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Folder'
            folder.mkdir()
            files = [folder / 'VTS_01_1.VOB', folder / 'VTS_01_2.VOB']
            for path in files:
                path.touch()

            jobs = build_upload_jobs(files, {'.vob'}, combine=True)

            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]['title'], 'Folder [combined]')

    def test_combine_true_disambiguates_extension_groups(self):
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
                files, {'.m2ts', '.avi'}, combine=True,
            )

            self.assertEqual(
                sorted(job['title'] for job in jobs),
                ['Folder (AVI) [combined]', 'Folder (M2TS) [combined]'],
            )

    def test_combine_true_single_file_uses_stem(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Folder'
            folder.mkdir()
            path = folder / 'clip.m2ts'
            path.touch()

            jobs = build_upload_jobs([path], {'.m2ts'}, combine=True)

            self.assertEqual(jobs[0]['title'], 'clip')

    def test_combine_true_uses_root_folder_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Root Folder'
            folder = root / 'Subfolder'
            folder.mkdir(parents=True)
            files = [
                folder / 'Event-100-00.AVI',
                folder / 'Event-100-01.AVI',
            ]
            for path in files:
                path.touch()

            jobs = build_upload_jobs(
                files, {'.avi'}, combine=True,
                root_folder=root, name_source='root',
            )

            self.assertEqual(jobs[0]['title'], 'Root Folder [combined]')

    def test_combine_true_uses_common_file_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Subfolder'
            folder.mkdir()
            files = [
                folder / 'Event-100-00.AVI',
                folder / 'Event-100-01.AVI',
            ]
            for path in files:
                path.touch()

            jobs = build_upload_jobs(
                files, {'.avi'}, combine=True, name_source='prefix',
            )

            self.assertEqual(
                jobs[0]['title'], 'Event-100 [combined]'
            )

    def test_prefix_uses_all_videos_in_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Subfolder'
            folder.mkdir()
            files = [
                folder / 'Event-100-00.AVI',
                folder / 'Event-100-01.AVI',
                folder / 'Event-100-02.MKV',
            ]
            for path in files:
                path.touch()

            jobs = build_upload_jobs(
                files, {'.avi', '.mkv'}, combine=True,
                name_source='prefix',
            )

            avi_job = next(
                job for job in jobs if job['files'][0].suffix == '.AVI'
            )
            self.assertEqual(
                avi_job['title'], 'Event-100 (AVI) [combined]'
            )

    def test_prefix_falls_back_to_folder_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Folder'
            folder.mkdir()
            files = [folder / 'A.avi', folder / 'B.avi']
            for path in files:
                path.touch()

            jobs = build_upload_jobs(
                files, {'.avi'}, combine=True, name_source='prefix',
            )

            self.assertEqual(jobs[0]['title'], 'Folder [combined]')

    def test_vob_prefix_uses_folder_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'Folder'
            folder.mkdir()
            files = [folder / 'VTS_01_1.VOB', folder / 'VTS_01_2.VOB']
            for path in files:
                path.touch()

            jobs = build_upload_jobs(
                files, {'.vob'}, combine=True, name_source='prefix',
            )

            self.assertEqual(jobs[0]['title'], 'Folder [combined]')



class ConvertedSourcePathTests(unittest.TestCase):
    def test_merged_vob_uses_folder_title(self):
        job = {
            'title': 'Family',
            'files': [Path('/v/1995/VTS_01_1.VOB'), Path('/v/1995/VTS_01_2.VOB')],
        }
        self.assertEqual(converted_source_path(job), Path('/v/1995/Family.mp4'))

    def test_output_folder_is_flat(self):
        job = {'title': 'Clip', 'files': [Path('/v/A/B/clip.AVI')]}
        self.assertEqual(
            converted_output_path(job, '/out'),
            Path('/out/Clip.mp4'),
        )

    def test_output_folder_is_flat_for_video_ts(self):
        job = {
            'title': 'Family',
            'files': [Path('/v/A/VIDEO_TS/VTS_01_1.VOB')],
        }
        self.assertEqual(
            converted_output_path(job, '/out'),
            Path('/out/Family.mp4'),
        )

    def test_output_path_overrides_source_folder(self):
        job = {
            'title': 'Clip',
            'files': [Path('/v/Clip.AVI')],
            'output_path': '/out/Clip.mp4',
        }
        self.assertEqual(converted_source_path(job), Path('/out/Clip.mp4'))

    def test_video_ts_uses_parent_folder(self):
        job = {'title': 'Family', 'files': [Path('/v/1995/VIDEO_TS/VTS_01_1.VOB')]}
        self.assertEqual(converted_source_path(job), Path('/v/1995/Family.mp4'))

    def test_single_file_uses_stem(self):
        job = {'title': 'Clip', 'files': [Path('/v/Clip.mp4')]}
        self.assertEqual(converted_source_path(job), Path('/v/Clip.mp4'))


class RemoveEmptyFoldersTests(unittest.TestCase):
    def test_removes_empty_parent_chain_but_not_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Root'
            leaf = root / 'A' / 'B'
            leaf.mkdir(parents=True)
            source = leaf / 'clip.AVI'
            source.touch()
            source.unlink()

            remove_empty_folders(source, root)

            self.assertFalse(leaf.exists())
            self.assertFalse(leaf.parent.exists())
            self.assertTrue(root.exists())

    def test_keeps_non_empty_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Root'
            leaf = root / 'A'
            leaf.mkdir(parents=True)
            (leaf / 'keep.txt').touch()
            source = leaf / 'clip.AVI'
            source.touch()
            source.unlink()

            remove_empty_folders(source, root)

            self.assertTrue(leaf.exists())

    def test_does_not_remove_outside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Root'
            other = Path(tmp) / 'Other'
            root.mkdir()
            other.mkdir()
            source = other / 'clip.AVI'
            source.touch()
            source.unlink()

            remove_empty_folders(source, root)

            self.assertTrue(other.exists())


if __name__ == '__main__':
    unittest.main()
