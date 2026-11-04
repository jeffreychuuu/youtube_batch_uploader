import json
import tempfile
import unittest
from pathlib import Path

from youtube_uploader.config import load_config


def _config_file(tmp, data):
    path = Path(tmp) / 'config.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


class GoldenConfigTests(unittest.TestCase):
    def test_defaults_are_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(_config_file(tmp, {}))
        self.assertFalse(config['export_converted_source'])
        self.assertFalse(config['remove_original_after_conversion'])
        self.assertFalse(config['remove_original_permanent'])

    def test_remove_original_requires_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                load_config(_config_file(tmp, {
                    'remove_original_after_conversion': True,
                    'export_converted_source': False,
                }))

    def test_flags_must_be_boolean(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                load_config(_config_file(tmp, {
                    'export_converted_source': 'yes',
                }))


if __name__ == '__main__':
    unittest.main()
