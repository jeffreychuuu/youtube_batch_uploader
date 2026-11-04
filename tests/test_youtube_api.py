import unittest

from youtube_uploader.youtube_api import build_video_body


class VideoBodyTests(unittest.TestCase):
    def test_uses_title_and_description(self):
        body = build_video_body('My Video', 'Removed ranges...', {
            'privacy_status': 'unlisted',
            'made_for_kids': False,
        })
        self.assertEqual(body['snippet']['title'], 'My Video')
        self.assertEqual(body['snippet']['description'], 'Removed ranges...')
        self.assertEqual(body['status']['privacyStatus'], 'unlisted')
        self.assertFalse(body['status']['selfDeclaredMadeForKids'])


if __name__ == '__main__':
    unittest.main()
