"""Filesystem paths and convert/upload config loading."""
import json
import sys
from pathlib import Path

# The package lives in a subfolder of the project directory. Settings live
# in `config/`, credentials and the conversion cache next to the entry
# points, and progress files in a separate `state/` folder.
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / 'config'
STATE_DIR = BASE_DIR / 'state'
CLIENT_SECRETS_FILE = BASE_DIR / 'client_secrets.json'
TOKEN_PICKLE_FILE = BASE_DIR / 'token.pickle'
STATE_FILE = STATE_DIR / 'upload_state.json'
CONVERT_STATE_FILE = STATE_DIR / 'convert_state.json'
CUTS_FILE = CONFIG_DIR / 'cuts_config.json'
CONVERT_CONFIG_FILE = CONFIG_DIR / 'convert_config.json'
UPLOAD_CONFIG_FILE = CONFIG_DIR / 'upload_config.json'
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
    # Combined title source: "folder", "root" or "prefix"
    "combined_name_source": "folder",
    # Encoder for golden conversion: "libx264" or "h264_videotoolbox" (macOS hardware)
    "convert_encoder": "libx264",
    # Quality value: CRF for libx264 (0-51), q:v for h264_videotoolbox (0-100)
    "convert_quality": 20,
    # Remove the original files after a verified conversion
    "remove_original_after_conversion": False,
    # Conversion-time removal method: false = move to Trash, true = delete permanently
    "remove_original_permanent": False,
}

# Cut settings: how cut re-encodes are produced.
DEFAULT_CUT_CONFIG = {
    # Encoder for cut re-encodes: "libx264" or "h264_videotoolbox" (macOS hardware)
    "cut_encoder": "libx264",
    # Quality value: CRF for libx264 (0-51), q:v for h264_videotoolbox (0-100)
    "cut_quality": 20,
}

# Upload settings: an independent scan and publish configuration.
DEFAULT_UPLOAD_CONFIG = {
    "root_folder": "/path/to/your/videos",
    # Golden files are MP4; add other extensions only if they are uploadable as-is
    "video_extensions": [".mp4"],
    "playlist_name": "My Playlist",
    "privacy_status": "unlisted",
    "made_for_kids": False,
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
    if config["combined_name_source"] not in ("folder", "root", "prefix"):
        sys.exit(
            "[錯誤] combined_name_source 只可以係 "
            "'folder'、'root' 或 'prefix'。"
        )
    if config["convert_encoder"] not in ("libx264", "h264_videotoolbox"):
        sys.exit(
            "[錯誤] convert_encoder 只可以係 'libx264' 或 'h264_videotoolbox'。"
        )
    if isinstance(config["convert_quality"], bool) or not isinstance(
        config["convert_quality"], int
    ):
        sys.exit("[錯誤] convert_quality 必須係整數。")
    max_quality = 51 if config["convert_encoder"] == "libx264" else 100
    if not 0 <= config["convert_quality"] <= max_quality:
        sys.exit(
            f"[錯誤] convert_quality 必須介乎 0 至 {max_quality}"
            f"（{config['convert_encoder']}）。"
        )
    for key in (
        "remove_original_after_conversion",
        "remove_original_permanent",
    ):
        if not isinstance(config[key], bool):
            sys.exit(f"[錯誤] {key} 必須係 true 或 false。")


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
