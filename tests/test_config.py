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
        self.assertFalse(config['remove_original_after_conversion'])
        self.assertFalse(config['remove_original_permanent'])
        self.assertTrue(config['combine'])

    def test_output_folder_defaults_to_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_convert_config(_write(tmp, 'convert_config.json', {}))
        self.assertEqual(config['output_folder'], '')

    def test_output_folder_must_be_a_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                load_convert_config(_write(tmp, 'convert_config.json', {
                    'output_folder': 123,
                }))

    def test_combined_name_source_defaults_to_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_convert_config(_write(tmp, 'convert_config.json', {}))
        self.assertEqual(config['combined_name_source'], 'folder')

    def test_rejects_unknown_combined_name_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                load_convert_config(_write(tmp, 'convert_config.json', {
                    'combined_name_source': 'filename',
                }))

    def test_encoder_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_convert_config(_write(tmp, 'convert_config.json', {}))
        self.assertEqual(config['convert_encoder'], 'libx264')
        self.assertEqual(config['convert_quality'], 20)

    def test_rejects_unknown_convert_encoder(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                load_convert_config(_write(tmp, 'convert_config.json', {
                    'convert_encoder': 'libx265',
                }))

    def test_rejects_invalid_convert_quality(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                load_convert_config(_write(tmp, 'convert_config.json', {
                    'convert_quality': 101,
                }))

    def test_remove_original_needs_no_export_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_convert_config(_write(tmp, 'convert_config.json', {
                'remove_original_after_conversion': True,
            }))
        self.assertTrue(config['remove_original_after_conversion'])

    def test_flags_must_be_boolean(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                load_convert_config(_write(tmp, 'convert_config.json', {
                    'remove_original_after_conversion': 'yes',
                }))

    def test_combine_must_be_boolean(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                load_convert_config(_write(tmp, 'convert_config.json', {
                    'combine': 'yes',
                }))



class UploadConfigTests(unittest.TestCase):
    def test_upload_config_is_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload = _write(tmp, 'upload_config.json', {
                'root_folder': '/videos',
                'video_extensions': ['.mp4'],
                'playlist_name': 'P',
            })
            config = load_upload_config(upload)
        self.assertEqual(config['root_folder'], '/videos')
        self.assertEqual(config['playlist_name'], 'P')
        self.assertNotIn('convert_extensions', config)

    def test_missing_config_uses_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload = _write(tmp, 'upload_config.json', {})
            config = load_upload_config(upload)
        self.assertEqual(config['privacy_status'], 'unlisted')
        self.assertEqual(config['video_extensions'][0], '.mp4')

    def test_video_extensions_must_start_with_dot(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload = _write(tmp, 'upload_config.json', {
                'video_extensions': ['mp4'],
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

    def test_flags_must_be_boolean(self):
        with tempfile.TemporaryDirectory() as tmp:
            upload = _write(tmp, 'upload_config.json', {
                'remove_local_after_upload': 'yes',
            })
            with self.assertRaises(SystemExit):
                load_upload_config(upload)

if __name__ == '__main__':
    unittest.main()
