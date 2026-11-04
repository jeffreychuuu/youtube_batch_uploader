import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_uploader.state import (
    get_golden_paths, load_convert_state, load_upload_state,
    record_golden_source, save_convert_state, save_upload_state,
)


class UploadStateTests(unittest.TestCase):
    def test_load_upload_state_returns_uploads_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'upload_state.json'
            path.write_text(json.dumps({
                'uploads': {'/a.mp4': {'parts': []}},
                'golden_sources': {'/a.mp4': {'title': 'A'}},
            }), encoding='utf-8')
            state = load_upload_state(path)
        self.assertEqual(state, {'uploads': {'/a.mp4': {'parts': []}}})

    def test_missing_upload_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = load_upload_state(Path(tmp) / 'upload_state.json')
        self.assertEqual(state, {'uploads': {}})

    def test_save_upload_state_drops_golden_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'upload_state.json'
            with patch('youtube_uploader.state.STATE_FILE', path):
                save_upload_state({
                    'uploads': {'/a.mp4': {'parts': []}},
                    'golden_sources': {'/a.mp4': {'title': 'A'}},
                })
            written = json.loads(path.read_text(encoding='utf-8'))
        self.assertNotIn('golden_sources', written)
        self.assertIn('uploads', written)


class ConvertStateTests(unittest.TestCase):
    def test_defaults_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / 'upload_state.json'
            with patch('youtube_uploader.state.STATE_FILE', missing):
                state = load_convert_state(Path(tmp) / 'convert_state.json')
        self.assertEqual(state, {'golden_sources': {}})

    def test_reads_convert_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'convert_state.json'
            path.write_text(json.dumps({
                'golden_sources': {'/a.mp4': {'title': 'A'}},
            }), encoding='utf-8')
            state = load_convert_state(path)
        self.assertIn('/a.mp4', state['golden_sources'])

    def test_legacy_fallback_from_upload_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload = Path(tmp) / 'upload_state.json'
            upload.write_text(json.dumps({
                'uploads': {},
                'golden_sources': {'/a.mp4': {'title': 'A'}},
            }), encoding='utf-8')
            with patch('youtube_uploader.state.STATE_FILE', upload):
                state = load_convert_state(Path(tmp) / 'convert_state.json')
        self.assertIn('/a.mp4', state['golden_sources'])

    def test_record_and_list_golden_source(self):
        state = {'golden_sources': {}}
        golden = Path('/tmp/movie.mp4')
        record_golden_source(state, golden, 'Movie', [Path('/tmp/VTS_01_1.VOB')])
        self.assertEqual(get_golden_paths(state), {str(golden.resolve())})
        self.assertEqual(
            state['golden_sources'][str(golden.resolve())]['title'], 'Movie'
        )

    def test_save_convert_state_writes_golden_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'convert_state.json'
            with patch('youtube_uploader.state.CONVERT_STATE_FILE', path):
                save_convert_state({
                    'golden_sources': {'/a.mp4': {'title': 'A'}},
                })
            written = json.loads(path.read_text(encoding='utf-8'))
        self.assertIn('/a.mp4', written['golden_sources'])


if __name__ == '__main__':
    unittest.main()
