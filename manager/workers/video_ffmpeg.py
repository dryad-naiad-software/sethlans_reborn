# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
ffmpeg command builders and runner for video assembly.

Every command is an argument list whose first element is the
parts-check resolved ffmpeg path; it is run with ``shell=False`` and
never string-formatted. Settings are checked against the hardcoded
allowlists of :mod:`workers.constants_video` before use.

A frame rate is an integer (Blender animations) or a ``(num, den)``
fraction (Unreal sequences, D19).  Sequence videos (D13, FR-SV-3..6) add
a ``-video_track_timescale`` that is a multiple of ``num`` for MP4 and MOV,
so every frame lasts the same whole number of ticks and shot videos join by
stream copy; the concat list is written by :func:`concat_list_text` and
its command always pairs ``-safe 0`` with ``-protocol_whitelist file``.
"""

import logging
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time

from .constants_video import (
    TIMESCALE_CONTAINERS, TRACK_TIMESCALE_BASE, VIDEO_AUDIO_CODECS, VIDEO_CODECS, VIDEO_CONTAINERS,
)

logger = logging.getLogger(__name__)

# The longest a single ffmpeg run may take.
FFMPEG_TIMEOUT_SECONDS = 3600
STDERR_KEEP = 2000
# At most this much of ffmpeg's stderr is kept (the end of it); the rest is discarded.
STDERR_TAIL_BYTES = 64 * 1024
RATE_NUM_MAX, RATE_DEN_MAX = 1_000_000, 1_001


def resolve_ffmpeg_executable():
    """Return parts-check resolved FFmpeg path (fallback ``'ffmpeg'``)."""
    from .services import parts_check
    return parts_check.get_status('ffmpeg').path or 'ffmpeg'


def rate_text(framerate):
    """``'24'`` for an int 1-120, ``'30000/1001'`` for a ``(num, den)`` of at most 120 fps.

    Raises ``ValueError`` for anything else.
    """
    if isinstance(framerate, int) and not isinstance(framerate, bool):
        if 1 <= framerate <= 120:
            return str(framerate)
    elif isinstance(framerate, (list, tuple)) and len(framerate) == 2 and all(
            isinstance(v, int) and not isinstance(v, bool) for v in framerate):
        num, den = framerate
        if 1 <= num <= RATE_NUM_MAX and 1 <= den <= RATE_DEN_MAX and den <= num <= 120 * den:
            return f'{num}/{den}'
    raise ValueError(f"Invalid framerate '{framerate}'. Must be int 1-120 or (num, den) up to 120 fps.")


def _checked(video_settings):
    """``(codec, container, crf)`` checked against the allowlists."""
    codec = video_settings.get('codec')
    container = video_settings.get('container')
    crf = video_settings.get('crf')
    if codec not in VIDEO_CODECS:
        raise ValueError(f"Invalid codec '{codec}'. Allowed: {sorted(VIDEO_CODECS)}")
    if container not in VIDEO_CONTAINERS:
        raise ValueError(f"Invalid container '{container}'. Allowed: {sorted(VIDEO_CONTAINERS)}")
    if not isinstance(crf, int) or not (0 <= crf <= 51):
        raise ValueError(f"Invalid crf '{crf}'. Must be int 0-51.")
    return codec, container, crf


def _codec_args(codec, crf, container=None):
    if codec == 'libx265' and container == 'mp4':
        # H.265 in MP4: the hvc1 tag that QuickTime, Resolve and browsers expect.
        return ['-pix_fmt', 'yuv420p', '-crf', str(crf), '-tag:v', 'hvc1']
    if codec == 'prores_ks':
        # ProRes: use HQ profile, no CRF, different pixel format
        return ['-profile:v', '3', '-pix_fmt', 'yuva444p10le']
    if codec == 'libvpx-vp9':
        # VP9: requires -b:v 0 for CRF mode
        return ['-pix_fmt', 'yuv420p', '-b:v', '0', '-crf', str(crf)]
    # H.264 / H.265
    return ['-pix_fmt', 'yuv420p', '-crf', str(crf)]


def track_timescale(rate):
    """D13: the smallest multiple of ``num`` of at least 90000; a frame then lasts exactly ``k * den`` ticks."""
    num = rate[0] if isinstance(rate, (list, tuple)) else int(rate)
    return math.ceil(TRACK_TIMESCALE_BASE / num) * num


def timescale_args(container, rate):
    """D13: ``-video_track_timescale`` (:func:`track_timescale`) for MP4 and MOV; nothing for WebM."""
    if container not in TIMESCALE_CONTAINERS:
        return []
    return ['-video_track_timescale', str(track_timescale(rate))]


def build_ffmpeg_command(frame_pattern, output_path, video_settings):
    """
    Build the ffmpeg argument list from video_settings.

    First argv element is the parts-check resolved path (single argv
    element — never string-formatted, never wrapped in a shell).
    Performs defense-in-depth validation against hardcoded allowlists.

    Raises ``ValueError`` if any setting fails allowlist validation.
    """
    codec, container, crf = _checked(video_settings)
    rate = rate_text(video_settings.get('framerate'))
    cmd = [resolve_ffmpeg_executable(), '-y', '-framerate', rate, '-i', frame_pattern, '-c:v', codec]
    cmd.extend(_codec_args(codec, crf, container))
    cmd.append(output_path)
    return cmd


def build_sequence_video_command(frame_pattern, output_path, video_settings, rate, start_number=0, frames=None):
    """FR-SV-3: frames to a shot video at ``-framerate num/den`` with the fixed track timescale (D13)."""
    codec, container, crf = _checked(video_settings)
    cmd = [resolve_ffmpeg_executable(), '-y', '-nostdin', '-framerate', rate_text(rate),
           '-start_number', str(int(start_number)), '-i', frame_pattern]
    if frames is not None:
        cmd.extend(['-frames:v', str(int(frames))])
    cmd.extend(['-c:v', codec, *_codec_args(codec, crf, container), *timescale_args(container, rate), output_path])
    return cmd


def concat_list_text(paths):
    """The concat demuxer list: ``file '<path>'`` per line, each ``'`` written as ``'\\''`` (FR-SV-4).

    Raises ``ValueError`` for a relative path or one with a line break.
    """
    lines = []
    for path in paths:
        text = str(path)
        if not os.path.isabs(text) or '\n' in text or '\r' in text or '\0' in text:
            raise ValueError("A concat path must be absolute and on one line.")
        lines.append("file '" + text.replace("'", "'\\''") + "'\n")
    return ''.join(lines)


def build_concat_command(list_path, output_path, container, rate):
    """D13: join shot videos by stream copy; ``-safe 0`` always with ``-protocol_whitelist file``."""
    if container not in VIDEO_CONTAINERS:
        raise ValueError(f"Invalid container '{container}'.")
    return [resolve_ffmpeg_executable(), '-y', '-nostdin', '-f', 'concat', '-safe', '0',
            '-protocol_whitelist', 'file', '-i', list_path, '-c', 'copy',
            *timescale_args(container, rate), output_path]


def build_mux_command(video_path, wav_path, output_path, container, rate):
    """FR-SV-6: one video and one audio stream, the video copied, the audio codec from the fixed map."""
    if container not in VIDEO_AUDIO_CODECS:
        raise ValueError(f"Invalid container '{container}'.")
    return [resolve_ffmpeg_executable(), '-y', '-nostdin', '-i', video_path, '-i', wav_path,
            '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', *VIDEO_AUDIO_CODECS[container],
            *timescale_args(container, rate), output_path]


def build_probe_command(video_path):
    """List a video's packets (``framecrc`` on stdout, by stream copy; U-8, ffmpeg only, no ffprobe)."""
    return [resolve_ffmpeg_executable(), '-hide_banner', '-nostdin', '-v', 'error',
            '-i', video_path, '-map', '0:v:0', '-c', 'copy', '-f', 'framecrc', '-']


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


def _tail(handle):
    size = handle.seek(0, os.SEEK_END)
    handle.seek(max(0, size - STDERR_TAIL_BYTES))
    return handle.read().decode('utf-8', 'replace')


def _stream(cmd, err, timeout, on_line):
    """Run ``cmd`` feeding each stdout line to ``on_line``; kill it at ``timeout`` seconds."""
    with subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err,
                          text=True, shell=False) as proc:
        started = time.monotonic()
        timer = threading.Timer(timeout, proc.kill)
        timer.daemon = True
        timer.start()
        try:
            for line in proc.stdout:
                on_line(line)
            code = proc.wait()
        except BaseException:
            proc.kill()
            raise
        finally:
            timer.cancel()
            timer.join()
    if time.monotonic() - started >= timeout:
        raise subprocess.TimeoutExpired(cmd, timeout)
    return code


def run_ffmpeg_output(cmd, subject, timeout=FFMPEG_TIMEOUT_SECONDS, on_line=None):
    """Run ffmpeg; return ``(returncode, the last 64 KiB of stderr)``, ``(None, message)`` on timeout.

    stdout is discarded, or each line goes to ``on_line`` as it comes.
    stderr goes to a temporary file, so a chatty run never fills memory.
    """
    logger.info("Running ffmpeg for %s: %s", subject, ' '.join(cmd))
    with tempfile.TemporaryFile() as err:
        try:
            if on_line is None:
                code = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
                                      timeout=timeout, shell=False).returncode
            else:
                code = _stream(cmd, err, timeout, on_line)
        except subprocess.TimeoutExpired:
            if timeout == FFMPEG_TIMEOUT_SECONDS:
                return None, 'FFmpeg timed out after 1 hour.'
            return None, f'FFmpeg timed out after {round(timeout)} seconds.'
        return code, _tail(err)


def run_ffmpeg(cmd, subject):
    """
    Run ffmpeg and return (success, error_message) tuple.

    ``subject`` names what is being built in the log lines, e.g.
    ``'animation 7'``. Returns (True, None) on success, (False,
    error_msg) on failure.
    """
    code, stderr = run_ffmpeg_output(cmd, subject)
    if code is None:
        return False, stderr
    if code != 0:
        logger.error("FFmpeg failed for %s (exit %d): %s", subject, code, stderr)
        return False, (stderr or 'Unknown ffmpeg error')[:STDERR_KEEP]
    return True, None
