import json
import tempfile
import unittest
from pathlib import Path

from youtube_uploader.cuts import (
    format_timestamp,
    get_job_cuts,
    load_cuts,
    normalise_cuts,
    parse_cut_ranges,
    parse_time,
)


class ParseTimeTests(unittest.TestCase):
    def test_plain_seconds(self):
        self.assertEqual(parse_time('90'), 90)

    def test_minutes_and_seconds(self):
        self.assertEqual(parse_time('1:05'), 65)

    def test_hours_minutes_and_seconds(self):
        self.assertEqual(parse_time('1:02:03'), 3723)

    def test_rejects_invalid_values(self):
        for text in ('1:60', '1:00:60', '1:60:00', 'abc', '1:2:3:4', ''):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_time(text)


class NormaliseCutsTests(unittest.TestCase):
    def test_sorts_ranges(self):
        self.assertEqual(
            normalise_cuts([(30, 40), (10, 20)]),
            [(10, 20), (30, 40)],
        )

    def test_merges_overlapping_ranges(self):
        self.assertEqual(normalise_cuts([(10, 30), (20, 40)]), [(10, 40)])

    def test_merges_adjacent_ranges(self):
        self.assertEqual(normalise_cuts([(10, 20), (20, 30)]), [(10, 30)])

    def test_ignores_empty_ranges(self):
        self.assertEqual(normalise_cuts([(10, 10), (20, 15)]), [])


class ParseCutRangesTests(unittest.TestCase):
    def test_accepts_supported_separators(self):
        self.assertEqual(
            parse_cut_ranges(
                ['1:00-2:00', '3:00–4:00', '5:00—6:00', '7:00~8:00', '9:00 to 10:00'],
                'Video',
            ),
            [(60, 120), (180, 240), (300, 360), (420, 480), (540, 600)],
        )

    def test_rejects_reversed_range(self):
        with self.assertRaises(SystemExit):
            parse_cut_ranges(['2:00-1:00'], 'Video')

    def test_rejects_malformed_range(self):
        with self.assertRaises(SystemExit):
            parse_cut_ranges(['not a range'], 'Video')


class LoadCutsTests(unittest.TestCase):
    def _load(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cuts.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            return load_cuts(path)

    def test_loads_flat_key_to_ranges(self):
        cuts = self._load({'Video': ['1:00-2:00']})
        self.assertEqual(cuts, {'Video': [(60, 120)]})

    def test_accepts_combined_titles(self):
        cuts = self._load({'Folder [combined]': ['1:00-2:00']})
        self.assertIn('Folder [combined]', cuts)

    def test_rejects_old_nested_format(self):
        with self.assertRaises(SystemExit):
            self._load({
                'folders': {
                    'Folder': {'original': ['1:00-2:00']}
                }
            })

    def test_rejects_non_list_value(self):
        with self.assertRaises(SystemExit):
            self._load({'Video': '1:00-2:00'})

    def test_rejects_malformed_range(self):
        with self.assertRaises(SystemExit):
            self._load({'Video': ['not a range']})


class GetJobCutsTests(unittest.TestCase):
    def test_matches_title_and_marks_used(self):
        job = {'title': 'Video', 'files': [Path('/tmp/Video.mp4')]}
        cuts_map = {'Video': [(10, 20)]}
        used = set()
        self.assertEqual(get_job_cuts(cuts_map, job, used), [(10, 20)])
        self.assertEqual(used, {'Video'})

    def test_no_match_returns_empty(self):
        job = {'title': 'Video', 'files': [Path('/tmp/Video.mp4')]}
        self.assertEqual(get_job_cuts({'Other': [(10, 20)]}, job), [])

    def test_golden_title_matches_combined_key(self):
        job = {'title': 'Folder [combined]', 'files': [Path('/a/Folder.mp4')]}
        cuts_map = {'Folder [combined]': [(10, 20)]}
        self.assertEqual(get_job_cuts(cuts_map, job), [(10, 20)])


class FormatTimestampTests(unittest.TestCase):
    def test_minutes(self):
        self.assertEqual(format_timestamp(65), '1:05')

    def test_hours(self):
        self.assertEqual(format_timestamp(3665), '1:01:05')

    def test_rounds(self):
        self.assertEqual(format_timestamp(59.6), '1:00')


if __name__ == '__main__':
    unittest.main()
