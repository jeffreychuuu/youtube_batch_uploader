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
    def test_unwraps_legacy_uploads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'upload_state.json'
            path.write_text(json.dumps({
                'uploads': {'/a.mp4': {'video_id': 'vid'}},
                'golden_sources': {'/a.mp4': {'title': 'A'}},
            }), encoding='utf-8')
            state = load_upload_state(path)
        self.assertEqual(state, {'/a.mp4': {'video_id': 'vid'}})

    def test_loads_flat_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'upload_state.json'
            path.write_text(json.dumps({
                '/a.mp4': {'video_id': 'vid'},
            }), encoding='utf-8')
            state = load_upload_state(path)
        self.assertEqual(state, {'/a.mp4': {'video_id': 'vid'}})

    def test_missing_upload_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = load_upload_state(Path(tmp) / 'upload_state.json')
        self.assertEqual(state, {})

    def test_flattens_legacy_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'upload_state.json'
            path.write_text(json.dumps({
                'uploads': {
                    '/a.mp4': {
                        'size': 1,
                        'parts': [
                            {'video_id': 'v1', 'playlist_added': True},
                            {'video_id': 'v2'},
                        ],
                    }
                },
            }), encoding='utf-8')
            state = load_upload_state(path)
        entry = state['/a.mp4']
        self.assertEqual(entry['video_id'], 'v1')
        self.assertTrue(entry['playlist_added'])
        self.assertNotIn('parts', entry)

    def test_save_writes_flat_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'upload_state.json'
            with patch('youtube_uploader.state.STATE_FILE', path):
                save_upload_state({'/a.mp4': {'video_id': 'vid'}})
            written = json.loads(path.read_text(encoding='utf-8'))
        self.assertNotIn('uploads', written)
        self.assertEqual(written['/a.mp4']['video_id'], 'vid')


class ConvertStateTests(unittest.TestCase):
    def test_defaults_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / 'upload_state.json'
            with patch('youtube_uploader.state.STATE_FILE', missing):
                state = load_convert_state(Path(tmp) / 'convert_state.json')
        self.assertEqual(state, {})

    def test_reads_flat_convert_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'convert_state.json'
            path.write_text(json.dumps({
                '/a.mp4': {'title': 'A', 'source_files': []},
            }), encoding='utf-8')
            state = load_convert_state(path)
        self.assertIn('/a.mp4', state)

    def test_reads_legacy_wrapped_convert_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'convert_state.json'
            path.write_text(json.dumps({
                'golden_sources': {'/a.mp4': {'title': 'A'}},
            }), encoding='utf-8')
            state = load_convert_state(path)
        self.assertIn('/a.mp4', state)

    def test_legacy_fallback_from_upload_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload = Path(tmp) / 'upload_state.json'
            upload.write_text(json.dumps({
                'uploads': {},
                'golden_sources': {'/a.mp4': {'title': 'A'}},
            }), encoding='utf-8')
            with patch('youtube_uploader.state.STATE_FILE', upload):
                state = load_convert_state(Path(tmp) / 'convert_state.json')
        self.assertIn('/a.mp4', state)

    def test_record_and_list_golden_source(self):
        state = {}
        golden = Path('/tmp/movie.mp4')
        record_golden_source(state, golden, 'Movie', [Path('/tmp/VTS_01_1.VOB')])
        self.assertEqual(get_golden_paths(state), {str(golden.resolve())})
        self.assertEqual(state[str(golden.resolve())]['title'], 'Movie')

    def test_save_writes_flat_convert_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'convert_state.json'
            with patch('youtube_uploader.state.CONVERT_STATE_FILE', path):
                save_convert_state({'/a.mp4': {'title': 'A'}})
            written = json.loads(path.read_text(encoding='utf-8'))
        self.assertNotIn('golden_sources', written)
        self.assertEqual(written['/a.mp4']['title'], 'A')


if __name__ == '__main__':
    unittest.main()
