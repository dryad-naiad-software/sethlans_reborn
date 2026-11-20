# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
ffmpeg command builders and runner for video assembly.

Every command is an argument list whose first element is the
parts-check resolved ffmpeg path; it is run with ``shell=False`` and
never string-formatted. Settings are checked against the hardcoded
allowlists of :mod:`workers.constants_video` before use.
"""

import logging
import os
import shutil
import subprocess

from .constants_video import VIDEO_CODECS, VIDEO_CONTAINERS

logger = logging.getLogger(__name__)

# The longest a single ffmpeg run may take.
FFMPEG_TIMEOUT_SECONDS = 3600


def resolve_ffmpeg_executable():
    """Return parts-check resolved FFmpeg path (fallback ``'ffmpeg'``)."""
    from .services import parts_check
    return parts_check.get_status('ffmpeg').path or 'ffmpeg'


def build_ffmpeg_command(frame_pattern, output_path, video_settings):
    """
    Build the ffmpeg argument list from video_settings.

    First argv element is the parts-check resolved path (single argv
    element — never string-formatted, never wrapped in a shell).
    Performs defense-in-depth validation against hardcoded allowlists.

    Raises ``ValueError`` if any setting fails allowlist validation.
    """
    codec = video_settings.get('codec')
    container = video_settings.get('container')
    framerate = video_settings.get('framerate')
    crf = video_settings.get('crf')

    if codec not in VIDEO_CODECS:
        raise ValueError(f"Invalid codec '{codec}'. Allowed: {sorted(VIDEO_CODECS)}")
    if container not in VIDEO_CONTAINERS:
        raise ValueError(f"Invalid container '{container}'. Allowed: {sorted(VIDEO_CONTAINERS)}")
    if not isinstance(framerate, int) or not (1 <= framerate <= 120):
        raise ValueError(f"Invalid framerate '{framerate}'. Must be int 1-120.")
    if not isinstance(crf, int) or not (0 <= crf <= 51):
        raise ValueError(f"Invalid crf '{crf}'. Must be int 0-51.")

    cmd = [
        resolve_ffmpeg_executable(), '-y',
        '-framerate', str(framerate),
        '-i', frame_pattern,
        '-c:v', codec,
    ]

    if codec == 'prores_ks':
        # ProRes: use HQ profile, no CRF, different pixel format
        cmd.extend(['-profile:v', '3', '-pix_fmt', 'yuva444p10le'])
    elif codec == 'libvpx-vp9':
        # VP9: requires -b:v 0 for CRF mode
        cmd.extend(['-pix_fmt', 'yuv420p', '-b:v', '0', '-crf', str(crf)])
    else:
        # H.264 / H.265
        cmd.extend(['-pix_fmt', 'yuv420p', '-crf', str(crf)])

    cmd.append(output_path)
    return cmd


def prepare_temp_frames(frame_files, temp_dir, extension):
    """
    Create sequentially-named symlinks (or copies on Windows) in temp_dir.

    Returns the frame pattern string for ffmpeg input.
    """
    for idx, src_path in frame_files:
        dest_name = f"frame_{idx:04d}{extension}"
        dest_path = os.path.join(temp_dir, dest_name)
        try:
            os.symlink(src_path, dest_path)
        except OSError:
            # Windows fallback: copy instead of symlink
            shutil.copy2(src_path, dest_path)

    return os.path.join(temp_dir, f"frame_%04d{extension}")


def run_ffmpeg(cmd, subject):
    """
    Run ffmpeg and return (success, error_message) tuple.

    ``subject`` names what is being built in the log lines, e.g.
    ``'animation 7'``. Returns (True, None) on success, (False,
    error_msg) on failure.
    """
    logger.info("Running ffmpeg for %s: %s", subject, ' '.join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
            text=True,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return False, 'FFmpeg timed out after 1 hour.'

    if result.returncode != 0:
        logger.error(
            "FFmpeg failed for %s (exit %d): %s",
            subject, result.returncode, result.stderr,
        )
        return False, (result.stderr or 'Unknown ffmpeg error')[:2000]

    return True, None
