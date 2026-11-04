import tempfile
import unittest
from pathlib import Path

from youtube_uploader.golden import mark_golden_jobs, skip_converted_source_jobs


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
                    str(golden.resolve()): {'title': 'Folder'}
                }
            }
            skipped = skip_converted_source_jobs(jobs, state)
        self.assertEqual(jobs[0]['skip_reason'], 'golden')
        self.assertEqual(skipped, ['Folder'])


if __name__ == '__main__':
    unittest.main()
