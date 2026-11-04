import json
import tempfile
import unittest
from pathlib import Path

from youtube_uploader.config import load_convert_config, load_upload_config


def _write(tmp, name, data):
    path = Path(tmp) / name
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


class ConvertConfigTests(unittest.TestCase):
    def test_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_convert_config(
                _write(tmp, 'convert_config.json', {})
            )
        self.assertFalse(config['export_converted_source'])
        self.assertFalse(config['remove_original_after_conversion'])
        self.assertFalse(config['remove_original_permanent'])
        self.assertEqual(config['combine_mode'], 'vob')
        self.assertEqual(config['combine_extensions'], ['.vob'])
        self.assertEqual(config['combined_names'], {})

    def test_remove_original_requires_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                load_convert_config(_write(tmp, 'convert_config.json', {
                    'remove_original_after_conversion': True,
                    'export_converted_source': False,
                }))

    def test_flags_must_be_boolean(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                load_convert_config(_write(tmp, 'convert_config.json', {
                    'export_converted_source': 'yes',
                }))

    def test_rejects_invalid_combine_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                load_convert_config(_write(tmp, 'convert_config.json', {
                    'combine_mode': 'all',
                }))

    def test_folder_mode_requires_combine_extensions_to_be_convertible(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                load_convert_config(_write(tmp, 'convert_config.json', {
                    'combine_mode': 'folder',
                    'combine_extensions': ['.m2ts'],
                    'convert_extensions': ['.vob'],
                }))

    def test_combine_extensions_must_start_with_dot(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                load_convert_config(_write(tmp, 'convert_config.json', {
                    'combine_extensions': ['vob'],
                }))

    def test_combined_names_must_be_object_of_strings(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                load_convert_config(_write(tmp, 'convert_config.json', {
                    'combined_names': {'Folder': 123},
                }))


class UploadConfigTests(unittest.TestCase):
    def test_upload_config_is_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload = _write(tmp, 'upload_config.json', {
                'root_folder': '/videos',
                'video_extensions': ['.mp4'],
                'convert_extensions': ['.vob'],
                'playlist_name': 'P',
            })
            config = load_upload_config(upload)
        self.assertEqual(config['root_folder'], '/videos')
        self.assertEqual(config['playlist_name'], 'P')
        self.assertNotIn('export_converted_source', config)

    def test_missing_config_uses_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload = _write(tmp, 'upload_config.json', {})
            config = load_upload_config(upload)
        self.assertEqual(config['privacy_status'], 'unlisted')
        self.assertEqual(config['video_extensions'][0], '.mp4')

    def test_convert_extensions_must_start_with_dot(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload = _write(tmp, 'upload_config.json', {
                'convert_extensions': ['vob'],
            })
            with self.assertRaises(SystemExit):
                load_upload_config(upload)

    def test_rejects_invalid_privacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload = _write(tmp, 'upload_config.json', {
                'privacy_status': 'secret',
            })
            with self.assertRaises(SystemExit):
                load_upload_config(upload)

    def test_part_duration_must_be_numeric(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload = _write(tmp, 'upload_config.json', {
                'part_duration_hours': 'one',
            })
            with self.assertRaises(SystemExit):
                load_upload_config(upload)

    def test_flags_must_be_boolean(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload = _write(tmp, 'upload_config.json', {
                'remove_local_after_upload': 'yes',
            })
            with self.assertRaises(SystemExit):
                load_upload_config(upload)

if __name__ == '__main__':
    unittest.main()
