import unittest
from pathlib import Path

from youtube_uploader.cuts import (
    format_timestamp,
    get_job_cuts,
    normalise_cuts,
    parse_cut_ranges,
    parse_time,
    part_cut_ranges,
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


class PartCutTests(unittest.TestCase):
    def setUp(self):
        self.job = {'title': 'Video', 'files': [Path('/tmp/Video.mp4')]}

    def test_part_offsets_are_added(self):
        cuts_map = {'Video [part 2]': [(300, 360)]}
        self.assertEqual(
            part_cut_ranges(cuts_map, self.job, 1),
            [(3899, 3959)],
        )

    def test_part_offsets_respect_part_length(self):
        cuts_map = {'Video [part 2]': [(300, 360)]}
        self.assertEqual(
            part_cut_ranges(cuts_map, self.job, 0.25),
            [(1199, 1259)],
        )

    def test_no_part_ranges_without_splitting(self):
        cuts_map = {'Video [part 2]': [(300, 360)]}
        self.assertEqual(part_cut_ranges(cuts_map, self.job, 0), [])


class GetJobCutsTests(unittest.TestCase):
    def test_combines_title_path_and_part_ranges(self):
        job = {'title': 'Video', 'files': [Path('/tmp/Video.mp4')]}
        resolved = str(Path('/tmp/Video.mp4').resolve())
        cuts_map = {
            'Video': [(10, 20)],
            resolved: [(30, 40)],
            'Video [part 2]': [(5, 6)],
        }
        used = set()
        self.assertEqual(
            get_job_cuts(cuts_map, job, 1, used),
            [(10, 20), (30, 40), (3604, 3605)],
        )
        self.assertEqual(used, {'Video', resolved, 'Video [part 2]'})


class FormatTimestampTests(unittest.TestCase):
    def test_minutes(self):
        self.assertEqual(format_timestamp(65), '1:05')

    def test_hours(self):
        self.assertEqual(format_timestamp(3665), '1:01:05')

    def test_rounds(self):
        self.assertEqual(format_timestamp(59.6), '1:00')


if __name__ == '__main__':
    unittest.main()
