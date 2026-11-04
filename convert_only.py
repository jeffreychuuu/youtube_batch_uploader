#!/usr/bin/env python3
"""Convert source videos to golden MP4s without uploading."""
from youtube_uploader.config import load_convert_config
from youtube_uploader.convert import run_convert_only

if __name__ == '__main__':
    run_convert_only(load_convert_config())
