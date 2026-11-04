import tempfile
import unittest
from pathlib import Path

from youtube_uploader.state import (
    get_golden_paths, load_state, migrate_completed_entry, record_golden_source,
)


class GoldenStateTests(unittest.TestCase):
    def test_load_state_adds_golden_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / 'upload_state.json'
            state_file.write_text('{"uploads": {}}', encoding='utf-8')
            state = load_state(state_file)
        self.assertEqual(state['golden_sources'], {})

    def test_record_and_list_golden_source(self):
        state = {'uploads': {}, 'golden_sources': {}}
        golden = Path('/tmp/movie.mp4')
        record_golden_source(state, golden, 'Movie', [Path('/tmp/VTS_01_1.VOB')])
        self.assertEqual(get_golden_paths(state), {str(golden.resolve())})
        self.assertEqual(
            state['golden_sources'][str(golden.resolve())]['title'], 'Movie'
        )

    def test_migrate_completed_entry_refreshes_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            golden = Path(tmp) / 'movie.mp4'
            golden.write_bytes(b'x' * 100)
            old_key = str((Path(tmp) / 'VTS_01_1.VOB').resolve())
            state = {
                'uploads': {
                    old_key: {
                        'parts': [{'video_id': 'abc', 'playlist_added': True}],
                        'cuts': [[10, 20]],
                        'size': 1,
                        'mtime': 1.0,
                    }
                },
                'golden_sources': {},
            }
            migrate_completed_entry(state, old_key, golden)
        new_entry = state['uploads'][str(golden.resolve())]
        self.assertEqual(new_entry['parts'][0]['video_id'], 'abc')
        self.assertEqual(new_entry['cuts'], [[10, 20]])
        self.assertEqual(new_entry['size'], 100)
        self.assertNotIn('local_removed', new_entry)

    def test_migrate_incomplete_entry_does_nothing(self):
        state = {'uploads': {'old': {'parts': []}}, 'golden_sources': {}}
        migrate_completed_entry(state, 'old', Path('/tmp/g.mp4'))
        self.assertNotIn(str(Path('/tmp/g.mp4').resolve()), state['uploads'])


if __name__ == '__main__':
    unittest.main()
