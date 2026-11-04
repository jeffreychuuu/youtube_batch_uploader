import json
import tempfile
import unittest
from pathlib import Path

from youtube_uploader.cuts import (
    build_cut_description,
    format_timestamp,
    get_job_cuts,
    load_cut_config,
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
            path = Path(tmp) / 'cuts_config.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            return load_cuts(path)

    def test_loads_flat_key_to_ranges(self):
        cuts = self._load({'Video': ['1:00-2:00']})
        self.assertEqual(cuts, {'Video': [(60, 120)]})

    def test_loads_first_batch_on_golden_timeline(self):
        cuts = self._load({'Video': [['1:00-2:00']]})
        self.assertEqual(cuts, {'Video': [(60, 120)]})

    def test_second_batch_uses_uploaded_timeline(self):
        cuts = self._load({
            'Video': [
                ['20:00-30:00'],
                ['25:00-27:00'],
            ]
        })
        self.assertEqual(cuts, {'Video': [(1200, 1800), (2100, 2220)]})

    def test_third_batch_maps_through_all_previous_batches(self):
        cuts = self._load({
            'Video': [
                ['10:00-20:00'],
                ['5:00-6:00'],
                ['5:00-7:00'],
            ]
        })
        self.assertEqual(cuts, {'Video': [(300, 480), (600, 1200)]})

    def test_batch_range_spanning_previous_cut_is_split(self):
        cuts = self._load({
            'Video': [
                ['10:00-20:00'],
                ['5:00-15:00'],
            ]
        })
        self.assertEqual(cuts, {'Video': [(300, 1500)]})

    def test_rejects_mixed_flat_and_batch_values(self):
        with self.assertRaises(SystemExit):
            self._load({'Video': ['1:00-2:00', ['3:00-4:00']]})

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

    def test_ignores_reserved_settings(self):
        cuts = self._load({
            'cut_encoder': 'h264_videotoolbox',
            'cut_quality': 65,
            'Video': ['1:00-2:00'],
        })
        self.assertEqual(cuts, {'Video': [(60, 120)]})


class CutSettingsTests(unittest.TestCase):
    def _load(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cuts_config.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            return load_cut_config(path)

    def test_defaults(self):
        settings, cuts = self._load({'Video': ['1:00-2:00']})
        self.assertEqual(settings, {
            'cut_encoder': 'libx264',
            'cut_quality': 20,
        })
        self.assertEqual(cuts, {'Video': [(60, 120)]})

    def test_reads_reserved_settings(self):
        settings, cuts = self._load({
            'cut_encoder': 'h264_videotoolbox',
            'cut_quality': 65,
            'Video': ['1:00-2:00'],
        })
        self.assertEqual(settings, {
            'cut_encoder': 'h264_videotoolbox',
            'cut_quality': 65,
        })
        self.assertEqual(cuts, {'Video': [(60, 120)]})

    def test_reads_wrapped_cuts(self):
        settings, cuts = self._load({
            'cut_encoder': 'h264_videotoolbox',
            'cut_quality': 65,
            'cuts': {'Video': ['1:00-2:00']},
        })
        self.assertEqual(settings, {
            'cut_encoder': 'h264_videotoolbox',
            'cut_quality': 65,
        })
        self.assertEqual(cuts, {'Video': [(60, 120)]})

    def test_wrapped_cuts_without_settings(self):
        settings, cuts = self._load({
            'cuts': {'Video': ['1:00-2:00']},
        })
        self.assertEqual(settings, {
            'cut_encoder': 'libx264',
            'cut_quality': 20,
        })
        self.assertEqual(cuts, {'Video': [(60, 120)]})

    def test_rejects_non_object_cuts(self):
        with self.assertRaises(SystemExit):
            self._load({'cuts': ['1:00-2:00']})

    def test_rejects_unknown_encoder(self):
        with self.assertRaises(SystemExit):
            self._load({'cut_encoder': 'libx265', 'Video': []})

    def test_rejects_invalid_quality(self):
        with self.assertRaises(SystemExit):
            self._load({'cut_quality': 101, 'Video': []})


class CutDescriptionTests(unittest.TestCase):
    TEMPLATE = "因違反 YouTube 政策，已剪走以下片段（共 {total}）：\n{cuts}"

    def test_includes_total_and_formatted_ranges(self):
        description = build_cut_description(
            self.TEMPLATE, [(1200, 1800), (2100, 2220)]
        )
        self.assertEqual(
            description,
            "因違反 YouTube 政策，已剪走以下片段（共 12 分鐘）：\n"
            "- 20:00 - 30:00\n"
            "- 35:00 - 37:00",
        )

    def test_total_includes_seconds(self):
        description = build_cut_description(self.TEMPLATE, [(60, 150)])
        self.assertIn("（共 1 分 30 秒）", description)

    def test_total_under_one_minute(self):
        description = build_cut_description(self.TEMPLATE, [(0, 30)])
        self.assertIn("（共 30 秒）", description)

    def test_no_cuts_gives_empty_description(self):
        self.assertEqual(build_cut_description(self.TEMPLATE, []), '')

    def test_empty_template_gives_empty_description(self):
        self.assertEqual(build_cut_description('', [(1, 2)]), '')


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
