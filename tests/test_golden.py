import tempfile
import unittest
from pathlib import Path

from youtube_uploader.golden import (
    ensure_unique_converted_targets, mark_golden_jobs,
    skip_converted_source_jobs,
)


class GoldenHelperTests(unittest.TestCase):
    def test_marks_recorded_single_file_job(self):
        jobs = [{'title': 'Folder', 'files': [Path('/a/Folder.mp4')]}]
        state = {
            'golden_sources': {
                str(Path('/a/Folder.mp4').resolve()): {'title': 'Folder'}
            }
        }
        mark_golden_jobs(jobs, state)
        self.assertTrue(jobs[0]['golden'])

    def test_skips_source_job_when_golden_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            vob = folder / 'VTS_01_1.VOB'
            vob.touch()
            golden = folder / 'Folder.mp4'
            golden.touch()
            jobs = [{
                'title': 'Folder',
                'files': [vob],
                'convert': True,
                'display_name': 'Folder',
            }]
            state = {
                'golden_sources': {
                    str(golden.resolve()): {
                        'title': 'Folder',
                        'source_files': [str(vob.resolve())],
                    }
                }
            }
            skipped = skip_converted_source_jobs(jobs, state)
        self.assertEqual(jobs[0]['skip_reason'], 'golden')
        self.assertEqual(skipped, ['Folder'])

    def test_skip_refuses_provenance_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source_a = folder / 'A.VOB'
            source_b = folder / 'B.VOB'
            source_a.touch()
            source_b.touch()
            golden = folder / 'Folder.mp4'
            golden.touch()
            jobs = [{
                'title': 'Folder',
                'files': [source_b],
                'convert': True,
                'display_name': 'B',
            }]
            state = {
                'golden_sources': {
                    str(golden.resolve()): {
                        'title': 'Folder',
                        'source_files': [str(source_a.resolve())],
                    }
                }
            }
            with self.assertRaises(SystemExit):
                skip_converted_source_jobs(jobs, state)

    def test_skip_allows_subset_of_recorded_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            sources = [folder / f'VTS_01_{index}.VOB' for index in range(1, 5)]
            for source in sources:
                source.touch()
            golden = folder / 'Folder.mp4'
            golden.touch()
            jobs = [{
                'title': 'Folder',
                'files': sources[2:],
                'convert': True,
                'display_name': 'Folder',
            }]
            state = {
                'golden_sources': {
                    str(golden.resolve()): {
                        'title': 'Folder',
                        'source_files': [str(path.resolve()) for path in sources],
                    }
                }
            }
            skipped = skip_converted_source_jobs(jobs, state)
        self.assertEqual(skipped, ['Folder'])

    def test_skip_refuses_source_not_in_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            recorded = [folder / 'VTS_01_1.VOB', folder / 'VTS_01_2.VOB']
            current = [folder / 'VTS_01_1.VOB', folder / 'VTS_01_3.VOB']
            for source in set(recorded + current):
                source.touch()
            golden = folder / 'Folder.mp4'
            golden.touch()
            jobs = [{
                'title': 'Folder',
                'files': current,
                'convert': True,
                'display_name': 'Folder',
            }]
            state = {
                'golden_sources': {
                    str(golden.resolve()): {
                        'title': 'Folder',
                        'source_files': [
                            str(path.resolve()) for path in recorded
                        ],
                    }
                }
            }
            with self.assertRaises(SystemExit):
                skip_converted_source_jobs(jobs, state)

    def test_skip_applies_to_non_convert_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / 'Folder.mkv'
            source.touch()
            golden = folder / 'Folder.mp4'
            golden.touch()
            jobs = [{
                'title': 'Folder',
                'files': [source],
                'convert': False,
                'display_name': 'Folder.mkv',
            }]
            state = {
                'golden_sources': {
                    str(golden.resolve()): {
                        'title': 'Folder',
                        'source_files': [str(source.resolve())],
                    }
                }
            }
            skipped = skip_converted_source_jobs(jobs, state)
        self.assertEqual(skipped, ['Folder.mkv'])

    def test_duplicate_converted_targets_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            jobs = [
                {
                    'title': 'Family',
                    'files': [folder / 'Family.mkv'],
                    'convert': True,
                    'display_name': 'Family.mkv',
                },
                {
                    'title': 'Family',
                    'files': [folder / 'Family.avi'],
                    'convert': True,
                    'display_name': 'Family.avi',
                },
            ]
            with self.assertRaises(SystemExit):
                ensure_unique_converted_targets(jobs)


if __name__ == '__main__':
    unittest.main()
