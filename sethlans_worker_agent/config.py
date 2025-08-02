# FILENAME: sethlans_worker_agent/config.py
# sethlans_worker_agent/config.py
"""
Configuration module for the Sethlans Reborn worker agent.

This module consolidates all configurable settings for the worker agent,
including API endpoints, operational intervals, file paths, and hardware
management flags. It is the single source of truth for the worker's behavior.

Configuration is loaded with the following priority (highest priority last):
1. Hardcoded defaults in this file.
2. Values from a 'config.ini' file in the same directory.
3. Environment variables (e.g., SETHLANS_MANAGER_PORT).
"""

import os
import sys
import platform
import logging
import configparser
from pathlib import Path

# --- Config File Loading ---
config_parser = configparser.ConfigParser()
config_file_path = Path(__file__).resolve().parent / 'config.ini'
if config_file_path.exists():
    config_parser.read(config_file_path)


# --- Helper function to get config value with override ---
def get_config_value(section, key, default, is_int=False):
    """
    Gets a configuration value, respecting the override hierarchy.
    1. Checks environment variable.
    2. Checks .ini file.
    3. Falls back to the hardcoded default.
    """
    env_var_name = f"SETHLANS_{section.upper()}_{key.upper()}"
    value = os.getenv(env_var_name)
    if value is not None:
        return int(value) if is_int else value

    if config_parser.has_option(section, key):
        if is_int:
            return config_parser.getint(section, key)
        return config_parser.get(section, key)

    return int(default) if is_int else default


# --- Manager API Configuration ---
MANAGER_PORT = get_config_value('manager', 'port', 7075, is_int=True)
MANAGER_HOST = get_config_value('manager', 'host', '127.0.0.1')
# The base URL for the central Django Manager's API.
MANAGER_API_URL = f"http://{MANAGER_HOST}:{MANAGER_PORT}/api/"


# --- Worker Operation Intervals ---
HEARTBEAT_INTERVAL_SECONDS = get_config_value('worker', 'heartbeat_interval', 30, is_int=True)
JOB_POLLING_INTERVAL_SECONDS = get_config_value('worker', 'polling_interval', 5, is_int=True)

# --- Worker Hardware Configuration ---
# These settings are mutually exclusive and can be set via environment variables.
FORCE_CPU_ONLY = os.getenv('SETHLANS_FORCE_CPU_ONLY', 'false').lower() == 'true'
FORCE_GPU_ONLY = os.getenv('SETHLANS_FORCE_GPU_ONLY', 'false').lower() == 'true'

if FORCE_CPU_ONLY and FORCE_GPU_ONLY:
    sys.stderr.write("ERROR: SETHLANS_FORCE_CPU_ONLY and SETHLANS_FORCE_GPU_ONLY are mutually exclusive. Set only one.\n")
    sys.exit(1)


# --- Worker Agent Paths ---
# The root directory of the entire project.
PROJECT_ROOT_FOR_WORKER = Path(__file__).resolve().parent.parent
# The root directory of the worker agent module.
WORKER_AGENT_DIR = Path(__file__).resolve().parent

# The path to a system-wide Blender executable. Currently not used.
SYSTEM_BLENDER_EXECUTABLE = None

# Directories for local storage managed by the worker agent.
MANAGED_TOOLS_DIR = WORKER_AGENT_DIR / 'managed_tools'
MANAGED_ASSETS_DIR = WORKER_AGENT_DIR / 'managed_assets'
WORKER_OUTPUT_DIR = WORKER_AGENT_DIR / 'worker_output'
WORKER_TEMP_DIR = WORKER_AGENT_DIR / 'temp'
WORKER_LOG_DIR = WORKER_AGENT_DIR / 'logs'


# Paths to test .blend files used in the end-to-end test suite.
TEST_BLEND_FILE_PATH = PROJECT_ROOT_FOR_WORKER / 'tests' / 'assets' / 'test_scene.blend'
BENCHMARK_BLEND_FILE_PATH = PROJECT_ROOT_FOR_WORKER / 'tests' / 'assets' / 'bmw27.blend'
ANIMATION_BLEND_FILE_PATH = PROJECT_ROOT_FOR_WORKER / 'tests' / 'assets' / 'animation.blend'


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

# The required major.minor LTS version series for the worker to install.
REQUIRED_LTS_VERSION_SERIES = "4.5"


# --- Platform and Architecture-specific Blender Download/Executable Mappings ---
# A dictionary mapping Python's platform.system()/platform.machine() output
# to the correct Blender download naming conventions and executable paths.
PLATFORM_BLENDER_MAP = {
    ('Windows', 'amd64'): {
        'download_suffix': 'windows-x64',
        'download_ext': '.zip',
        'executable_path_in_folder': 'blender.exe'
    },
    ('Windows', 'arm64'): {
        'download_suffix': 'windows-arm64',
        'download_ext': '.zip',
        'executable_path_in_folder': 'blender.exe'
    },
    ('Linux', 'x86_64'): {
        'download_suffix': 'linux-x64',
        'download_ext': '.tar.xz',
        'executable_path_in_folder': 'blender'
    },
    ('Linux', 'aarch64'): {
        'download_suffix': 'linux-arm64',
        'download_ext': '.tar.xz',
        'executable_path_in_folder': 'blender'
    },
    ('Darwin', 'x86_64'): {
        'download_suffix': 'macos-x64',
        'download_ext': '.dmg',
        'executable_path_in_folder': 'blender.app/Contents/MacOS/blender'
    },
    ('Darwin', 'arm64'): {
        'download_suffix': 'macos-arm64',
        'download_ext': '.dmg',
        'executable_path_in_folder': 'blender.app/Contents/MacOS/blender'
    }
}

# The specific details for the current platform and architecture.
CURRENT_PLATFORM_BLENDER_DETAILS = PLATFORM_BLENDER_MAP.get((platform.system(), platform.machine().lower()))
if not CURRENT_PLATFORM_BLENDER_DETAILS:
    print(
        f"[WARNING] Unsupported OS/Architecture for Blender management: ({platform.system()}, {platform.machine().lower()}). Auto-download may not work.")


def configure_worker_logging(log_level_str="INFO"):
    """
    Configures the basic logging for the worker agent.

    This function sets up a root logger with a specific format and a configurable
    log level. This should be called once at application startup.

    Args:
        log_level_str (str): The desired logging level as a string (e.g., 'DEBUG', 'INFO').
    """
    log_level = getattr(logging, log_level_str.upper(), logging.INFO)

    if not logging.root.handlers:
        logging.basicConfig(
            level=log_level,
            format='[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )