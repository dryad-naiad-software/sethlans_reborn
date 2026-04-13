# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Worker agent configuration — single source of truth.

Precedence (FR-28): env vars > JSON config_store > legacy config.ini >
defaults. Exception: ``manager.api_token``, ``manager.cert_fingerprint``,
and ``manager.manager_id`` are NOT env-overridable (logged + dropped).
"""

import os
import sys
import platform
import logging
import configparser
from pathlib import Path

from sethlans_worker_agent import config_store

logger = logging.getLogger(__name__)

# --- Root Paths ---
WORKER_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = WORKER_ROOT.parent

# --- Config File Loading ---
config_parser = configparser.ConfigParser()
config_file_path = WORKER_ROOT / 'config.ini'
_legacy_ini_loaded = False
if config_file_path.exists():
    config_parser.read(config_file_path)
    _legacy_ini_loaded = True
    logger.info(
        "DEPRECATION WARNING: reading legacy config.ini at %s. "
        "Writes now go to the JSON config_store. (FR-29)",
        config_file_path,
    )

_json_config = config_store.load()

# FR-28 exception list: post-enrollment credential triple.
_ENV_OVERRIDE_EXCEPTIONS = frozenset(
    {"api_token", "cert_fingerprint", "manager_id"}
)


def _env_var_name(section, key):
    return f"SETHLANS_{section.upper()}_{key.upper()}"


def get_config_value(section, key, default, is_int=False):
    """Read a config value: env var > JSON config_store > INI > default."""
    env_var_name = _env_var_name(section, key)
    env_blocked = (
        section == "manager"
        and key in _ENV_OVERRIDE_EXCEPTIONS
        and env_var_name in os.environ
    )
    if env_blocked:
        logger.warning(
            "ignoring %s; %s is managed by the enrollment flow",
            env_var_name, key,
        )
    else:
        value = os.getenv(env_var_name)
        if value is not None:
            return int(value) if is_int else value
    json_section = _json_config.get(section)
    if isinstance(json_section, dict) and key in json_section:
        value = json_section[key]
        if is_int:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
        else:
            return value
    if config_parser.has_option(section, key):
        if is_int:
            return config_parser.getint(section, key)
        return config_parser.get(section, key)
    return int(default) if is_int else default


def _validate_int(name, value, default, min_val=None, max_val=None):
    """Validate an int config value against optional bounds."""
    if not isinstance(value, int):
        logger.warning(
            "Config '%s' has non-integer value '%s'. Falling back to default: %s",
            name, value, default,
        )
        return default
    if min_val is not None and value < min_val:
        logger.warning(
            "Config '%s' value %d below minimum %d. Using default: %d",
            name, value, min_val, default,
        )
        return default
    if max_val is not None and value > max_val:
        logger.warning(
            "Config '%s' value %d above maximum %d. Using default: %d",
            name, value, max_val, default,
        )
        return default
    return value


def _validate_non_empty_string(name, value, default):
    """Validate that a string config value is non-empty."""
    if not isinstance(value, str) or not value.strip():
        logger.warning(
            "Config '%s' is empty or not a string. Using default: '%s'",
            name, default,
        )
        return default
    return value


# --- Manager API Configuration ---
MANAGER_PORT = get_config_value('manager', 'port', 8080, is_int=True)
MANAGER_PORT = _validate_int('manager.port', MANAGER_PORT, 8080, 1, 65535)
MANAGER_HOST = get_config_value('manager', 'host', '127.0.0.1')
MANAGER_HOST = _validate_non_empty_string('manager.host', MANAGER_HOST, '127.0.0.1')
# The base URL for the central Django Manager's API.
MANAGER_API_URL = f"https://{MANAGER_HOST}:{MANAGER_PORT}/api/"

# --- Manager Authentication ---
# SHA-256 fingerprint of the manager's TLS certificate, received during
# enrollment. Used by PinningHTTPAdapter to verify the server cert.
CERT_FINGERPRINT = get_config_value('manager', 'cert_fingerprint', '')
# Permanent API token received after successful enrollment. Initially empty.
API_TOKEN = get_config_value('manager', 'api_token', '')
# Unique manager identifier written during enrollment (FR-25).
MANAGER_ID = get_config_value('manager', 'manager_id', '')


# --- Worker Operation Intervals ---
HEARTBEAT_INTERVAL_SECONDS = get_config_value('worker', 'heartbeat_interval', 30, is_int=True)
HEARTBEAT_INTERVAL_SECONDS = _validate_int(
    'worker.heartbeat_interval', HEARTBEAT_INTERVAL_SECONDS, 30, 1, 3600
)
JOB_POLLING_INTERVAL_SECONDS = get_config_value('worker', 'polling_interval', 5, is_int=True)
JOB_POLLING_INTERVAL_SECONDS = _validate_int(
    'worker.polling_interval', JOB_POLLING_INTERVAL_SECONDS, 5, 1, 3600
)

# --- Worker Hardware Configuration ---
# These settings are mutually exclusive and can be set via environment variables.
FORCE_CPU_ONLY = os.getenv('SETHLANS_FORCE_CPU_ONLY', 'false').lower() == 'true'
FORCE_GPU_ONLY = os.getenv('SETHLANS_FORCE_GPU_ONLY', 'false').lower() == 'true'
# Allow specifying a single GPU index for all jobs on this worker
FORCE_GPU_INDEX = os.getenv('SETHLANS_FORCE_GPU_INDEX')

# GPU Mode: "split" (N GPUs -> N slots) or "combined" (N GPUs -> 1 slot).
_VALID_GPU_MODES = {'split', 'combined'}
_raw_gpu_mode = os.getenv('SETHLANS_GPU_MODE', 'split').strip().lower()
if _raw_gpu_mode not in _VALID_GPU_MODES:
    sys.stderr.write(
        "ERROR: SETHLANS_GPU_MODE must be one of "
        f"{sorted(_VALID_GPU_MODES)}. Got: '{_raw_gpu_mode}'.\n"
    )
    sys.exit(1)
GPU_MODE = _raw_gpu_mode

# Legacy SETHLANS_GPU_SPLIT_MODE is no longer recognized. Warn once on
# startup if the user still has it set, but continue normally.
if 'SETHLANS_GPU_SPLIT_MODE' in os.environ:
    logger.warning(
        "SETHLANS_GPU_SPLIT_MODE is no longer recognized. Use "
        "SETHLANS_GPU_MODE=split (default) or SETHLANS_GPU_MODE=combined. "
        "Continuing with the current SETHLANS_GPU_MODE value: '%s'.",
        GPU_MODE,
    )

# CPU threads for rendering. 0 = auto (cores - 1). Capped at (cores - 1)
# by the worker capacity module; values above the ceiling are silently capped.
CPU_THREADS = get_config_value('worker', 'cpu_threads', 0, is_int=True)
CPU_THREADS = _validate_int('worker.cpu_threads', CPU_THREADS, 0, 0, 1024)


if FORCE_CPU_ONLY and FORCE_GPU_ONLY:
    sys.stderr.write("ERROR: SETHLANS_FORCE_CPU_ONLY and SETHLANS_FORCE_GPU_ONLY are mutually exclusive. Set only one.\n")
    sys.exit(1)

if FORCE_GPU_INDEX is not None and GPU_MODE == 'combined':
    sys.stderr.write(
        "ERROR: SETHLANS_FORCE_GPU_INDEX and SETHLANS_GPU_MODE=combined "
        "are mutually exclusive. Pinning to one physical GPU index "
        "contradicts using all GPUs in one invocation. "
        "Set only one of these variables.\n"
    )
    sys.exit(1)


# --- Worker TLS Configuration ---
TLS_CERT_FILE = get_config_value('worker_tls', 'cert_file', '')
TLS_KEY_FILE = get_config_value('worker_tls', 'key_file', '')
# --- Worker Web UI Configuration ---
UI_ENABLED = get_config_value('worker', 'ui_enabled', 'true')
UI_ENABLED = UI_ENABLED.lower() in ('true', '1', 'yes') if isinstance(UI_ENABLED, str) else bool(UI_ENABLED)
UI_PORT = get_config_value('worker', 'ui_port', 8081, is_int=True)
UI_PORT = _validate_int('worker.ui_port', UI_PORT, 8081, 1, 65535)
UI_BIND_ADDRESS = get_config_value('worker', 'ui_bind_address', '0.0.0.0')
UI_PASSWORD_HASH = get_config_value('worker', 'ui_password_hash', '')
UI_PASSWORD_SALT = get_config_value('worker', 'ui_password_salt', '')


# --- Idle Detection & Scheduling Configuration (FR-1 through FR-8) ---
from sethlans_worker_agent.config_idle import (  # noqa: E402
    load_idle_config, load_schedule_config, get_schedule_config_live,
)

_idle_cfg = load_idle_config(get_config_value, _validate_int)
IDLE_DETECTION_ENABLED = _idle_cfg['IDLE_DETECTION_ENABLED']
IDLE_THRESHOLD_SECONDS = _idle_cfg['IDLE_THRESHOLD_SECONDS']
IDLE_GPU_UTILIZATION_THRESHOLD = _idle_cfg['IDLE_GPU_UTILIZATION_THRESHOLD']
IDLE_CPU_UTILIZATION_THRESHOLD = _idle_cfg['IDLE_CPU_UTILIZATION_THRESHOLD']
IDLE_SLOW_PATH_THRESHOLD_SECONDS = _idle_cfg['IDLE_SLOW_PATH_THRESHOLD_SECONDS']
IDLE_GRACE_PERIOD_CAP_SECONDS = _idle_cfg['IDLE_GRACE_PERIOD_CAP_SECONDS']
IDLE_CREATIVE_APP_NAMES = _idle_cfg['IDLE_CREATIVE_APP_NAMES']

_INITIAL_SCHEDULE = load_schedule_config(_json_config)


def get_schedule_config():
    """Return the claim_window config, re-reading from the store."""
    return get_schedule_config_live(config_store)


# --- Worker Agent Paths ---
# The root directory of the worker agent module (used only for legacy
# fallbacks and for locating the bundled detect_gpus.py script).
WORKER_AGENT_DIR = Path(__file__).resolve().parent

# The path to a system-wide Blender executable. Currently not used.
SYSTEM_BLENDER_EXECUTABLE = None

# FR-24a: Directories now live under the per-OS user data dir instead
# of inside the source tree so packaged installs in Program Files /
# /opt/sethlans are not treated as writable.
_DATA_DIR = config_store.get_data_dir()
MANAGED_TOOLS_DIR = _DATA_DIR / 'tools'
MANAGED_ASSETS_DIR = _DATA_DIR / 'assets'
WORKER_OUTPUT_DIR = _DATA_DIR / 'output'
WORKER_TEMP_DIR = _DATA_DIR / 'temp'
WORKER_LOG_DIR = _DATA_DIR / 'logs'
FAILED_UPLOADS_DIR = _DATA_DIR / 'failed_uploads'


# Paths to test .blend files used in the end-to-end test suite.
TEST_BLEND_FILE_PATH = REPO_ROOT / 'tests' / 'assets' / 'test_scene.blend'
BENCHMARK_BLEND_FILE_PATH = REPO_ROOT / 'tests' / 'assets' / 'bmw27.blend'
ANIMATION_BLEND_FILE_PATH = REPO_ROOT / 'tests' / 'assets' / 'animation.blend'


# --- Tool Discovery & Download Configuration ---
# The base URL for the official Blender downloads.
BLENDER_RELEASES_URL = "https://download.blender.org/release/"

# A list of mirror URLs for redundant download sources.
BLENDER_MIRROR_BASE_URLS = [
    "https://mirror.clarkson.edu/blender/release/",
    "http://ftp.halifax.rwth-aachen.de/blender/release/",
    "http://ftp.nluug.nl/pub/graphics/blender/release/",
]

# The local file path for the cached list of available Blender versions.
BLENDER_VERSIONS_CACHE_FILE = MANAGED_TOOLS_DIR / 'blender_versions_cache.json'


# Keyed by (platform.system(), platform.machine().lower()).
_W = {'download_ext': '.zip', 'executable_path_in_folder': 'blender.exe'}
_L = {'download_ext': '.tar.xz', 'executable_path_in_folder': 'blender'}
_M = {'download_ext': '.dmg', 'executable_path_in_folder': 'blender.app/Contents/MacOS/blender'}
PLATFORM_BLENDER_MAP = {
    ('Windows', 'amd64'): {**_W, 'download_suffix': 'windows-x64'},
    ('Windows', 'arm64'): {**_W, 'download_suffix': 'windows-arm64'},
    ('Linux', 'x86_64'): {**_L, 'download_suffix': 'linux-x64'},
    ('Linux', 'aarch64'): {**_L, 'download_suffix': 'linux-arm64'},
    ('Darwin', 'x86_64'): {**_M, 'download_suffix': 'macos-x64'},
    ('Darwin', 'arm64'): {**_M, 'download_suffix': 'macos-arm64'},
}

CURRENT_PLATFORM_BLENDER_DETAILS = PLATFORM_BLENDER_MAP.get(
    (platform.system(), platform.machine().lower()))
if not CURRENT_PLATFORM_BLENDER_DETAILS:
    print(f"[WARNING] Unsupported OS/Arch for Blender: "
          f"({platform.system()}, {platform.machine().lower()}).")


def configure_worker_logging(level="INFO"):
    """Configure root logging (call once at startup)."""
    lvl = getattr(logging, level.upper(), logging.INFO)
    if not logging.root.handlers:
        logging.basicConfig(
            level=lvl,
            format='[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )
