#!/usr/bin/env python3
"""Run conversion, then upload, using two independent configs."""
from youtube_uploader.cli import main
from youtube_uploader.config import load_convert_config, load_upload_config
from youtube_uploader.convert import run_convert_only

if __name__ == '__main__':
    run_convert_only(load_convert_config())
    main(load_upload_config())
