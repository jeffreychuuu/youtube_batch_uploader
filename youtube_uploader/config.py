"""Filesystem paths and convert/upload config loading."""
import json
import sys
from pathlib import Path

# The package lives in a subfolder of the project directory, so keep user files
# (config, credentials, state) next to the batch_upload.py entry point.
BASE_DIR = Path(__file__).resolve().parent.parent
CLIENT_SECRETS_FILE = BASE_DIR / 'client_secrets.json'
TOKEN_PICKLE_FILE = BASE_DIR / 'token.pickle'
STATE_FILE = BASE_DIR / 'upload_state.json'
CUTS_FILE = BASE_DIR / 'cuts.json'
CONVERT_CONFIG_FILE = BASE_DIR / 'convert_config.json'
UPLOAD_CONFIG_FILE = BASE_DIR / 'upload_config.json'
# Cache for MP4 conversions; safe to delete between runs
CONVERTED_DIR = BASE_DIR / '.converted'

# ==================== Default settings ====================
# Convert settings: what to scan and how to turn it into golden MP4s.
DEFAULT_CONVERT_CONFIG = {
    "root_folder": "/path/to/your/videos",
    # Extensions converted to MP4 with ffmpeg (VOB needs this; add e.g. ".avi"
    # here if you want every AVI transcoded for compatibility). The convert
    # script scans exactly these extensions.
    "convert_extensions": [".vob"],
    # Merge files that belong together: VOB title sets, and same-folder files
    # of the same convert extension. false = every file is its own job.
    "combine": True,
    # Persist the converted MP4 next to the source and reuse it as the source of record
    "export_converted_source": False,
    # Remove the original files after a verified conversion
    "remove_original_after_conversion": False,
    # Conversion-time removal method: false = move to Trash, true = delete permanently
    "remove_original_permanent": False,
}

# Upload settings: an independent scan and publish configuration.
DEFAULT_UPLOAD_CONFIG = {
    "root_folder": "/path/to/your/videos",
    # Golden files are MP4; add other extensions only if they are uploadable as-is
    "video_extensions": [".mp4"],
    "playlist_name": "My Playlist",
    "privacy_status": "unlisted",
    "made_for_kids": False,
    # Split long videos into parts of this many hours (0 = never split)
    "part_duration_hours": 1,
    "chunk_size_mb": 32,
    "max_retries": 5,
    # Whether to remove local video files after a successful upload
    "remove_local_after_upload": False,
    # Removal method: false = move to Trash (recoverable), true = delete permanently
    "remove_local_permanent": False,
}


def _load_json(config_file, defaults):
    """Load a JSON config and merge it with the defaults"""
    config = dict(defaults)
    if not config_file.exists():
        return config

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            user_config = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        sys.exit(f"[錯誤] 無法讀取 {config_file.name}：{e}")

    if not isinstance(user_config, dict):
        sys.exit(f"[錯誤] {config_file.name} 格式錯誤，最外層必須係 JSON object。")

    unknown = sorted(set(user_config) - set(defaults))
    if unknown:
        print(f"[警告] {config_file.name} 有未知設定項目，已忽略：{', '.join(unknown)}")

    config.update({k: v for k, v in user_config.items() if k in defaults})
    return config


def _validate_convert(config):
    if not isinstance(config["convert_extensions"], list):
        sys.exit("[錯誤] convert_extensions 必須係 JSON 陣列，例如 [\".vob\"]。")
    if any(
        not isinstance(ext, str) or not ext.startswith('.')
        for ext in config["convert_extensions"]
    ):
        sys.exit("[錯誤] convert_extensions 每項都必須係以 '.' 開頭的字串，例如 \".vob\"。")
    if not isinstance(config["combine"], bool):
        sys.exit("[錯誤] combine 必須係 true 或 false。")
    for key in (
        "export_converted_source",
        "remove_original_after_conversion",
        "remove_original_permanent",
    ):
        if not isinstance(config[key], bool):
            sys.exit(f"[錯誤] {key} 必須係 true 或 false。")
    if (
        config["remove_original_after_conversion"]
        and not config["export_converted_source"]
    ):
        sys.exit(
            "[錯誤] remove_original_after_conversion 需要 "
            "export_converted_source 同時設為 true。"
        )


def _validate_upload(config):
    if not isinstance(config["video_extensions"], list):
        sys.exit("[錯誤] video_extensions 必須係 JSON 陣列，例如 [\".mp4\"]。")
    if any(
        not isinstance(ext, str) or not ext.startswith('.')
        for ext in config["video_extensions"]
    ):
        sys.exit("[錯誤] video_extensions 每項都必須係以 '.' 開頭的字串，例如 \".mp4\"。")
    if config["privacy_status"] not in ("public", "unlisted", "private"):
        sys.exit("[錯誤] privacy_status 只可以係 'public'、'unlisted' 或 'private'。")
    part_hours = config["part_duration_hours"]
    if isinstance(part_hours, bool) or not isinstance(part_hours, (int, float)):
        sys.exit("[錯誤] part_duration_hours 必須係數字（例如 1 表示每 1 小時一段）。")
    if not 0 <= part_hours <= 12:
        sys.exit("[錯誤] part_duration_hours 必須介乎 0 至 12（0 = 不分割）。")
    for key in ("chunk_size_mb", "max_retries"):
        if isinstance(config[key], bool) or not isinstance(config[key], int):
            sys.exit(f"[錯誤] {key} 必須係整數。")
    for key in ("remove_local_after_upload", "remove_local_permanent"):
        if not isinstance(config[key], bool):
            sys.exit(f"[錯誤] {key} 必須係 true 或 false。")


def load_convert_config(config_file=CONVERT_CONFIG_FILE):
    """Load convert_config.json and merge it with the defaults"""
    config = _load_json(config_file, DEFAULT_CONVERT_CONFIG)
    _validate_convert(config)
    return config


def load_upload_config(config_file=UPLOAD_CONFIG_FILE):
    """Load upload_config.json; fully independent from the convert config"""
    config = _load_json(config_file, DEFAULT_UPLOAD_CONFIG)
    _validate_upload(config)
    return config
