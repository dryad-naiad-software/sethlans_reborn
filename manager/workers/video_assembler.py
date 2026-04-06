# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Video assembly engine for animations.

Runs ffmpeg in a background thread to assemble rendered frames into
a video file. Uses a concurrency semaphore to limit the number of
simultaneous ffmpeg processes.
"""

import logging
import os
import shutil
import subprocess
import tempfile
import threading

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils.text import slugify

from .constants import (
    VIDEO_CODECS, VIDEO_CONTAINERS, FORMAT_EXTENSIONS, RenderSettings,
)

logger = logging.getLogger(__name__)

_max_concurrent = getattr(settings, 'SETHLANS_MAX_CONCURRENT_ASSEMBLIES', 2)
_assembly_semaphore = threading.Semaphore(_max_concurrent)


def build_ffmpeg_command(frame_pattern, output_path, video_settings):
    """
    Build the ffmpeg argument list from video_settings.

    Performs defense-in-depth validation of all settings values against
    hardcoded allowlists before constructing the command.

    Args:
        frame_pattern: Path pattern for input frames (e.g., /tmp/dir/frame_%04d.png).
        output_path: Path for the output video file.
        video_settings: Dict with keys: codec, container, framerate, crf.

    Returns:
        list[str]: The ffmpeg command as a list of arguments.

    Raises:
        ValueError: If any setting fails allowlist validation.
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
        'ffmpeg', '-y',
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


def _validate_path_in_media_root(file_path):
    """Check that a resolved file path is within MEDIA_ROOT."""
    real_path = os.path.realpath(file_path)
    media_root = os.path.realpath(settings.MEDIA_ROOT)
    return real_path.startswith(media_root)


def _collect_frame_files(animation):
    """
    Collect completed frame file paths in frame-number order.

    For tiled animations, uses AnimationFrame.output_file.
    For non-tiled animations, uses Job.output_file.

    Returns:
        list[tuple[int, str]]: List of (index, abs_path) tuples.
    """
    from .constants import TilingConfiguration
    from .models import AnimationFrame, Job, JobStatus

    frame_files = []
    if animation.tiling_config != TilingConfiguration.NONE:
        frames = AnimationFrame.objects.filter(
            animation_id=animation.pk, status='DONE'
        ).order_by('frame_number')
        for idx, frame in enumerate(frames, start=1):
            if frame.output_file:
                frame_files.append((idx, frame.output_file.path))
    else:
        jobs = Job.objects.filter(
            animation_id=animation.pk, status=JobStatus.DONE
        ).order_by('start_frame')
        for idx, job in enumerate(jobs, start=1):
            if job.output_file:
                frame_files.append((idx, job.output_file.path))

    return frame_files


def _prepare_temp_frames(frame_files, temp_dir, extension):
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


def assemble_animation_video(animation_id):
    """
    Assemble rendered frames into a video file using ffmpeg.

    This function is intended to run in a background daemon thread.
    It acquires the concurrency semaphore, collects frame files,
    runs ffmpeg, and saves the result.

    All database updates use .filter().update() to avoid triggering
    post_save signals.

    Args:
        animation_id: Primary key of the Animation to assemble.
    """
    from .apps import WorkersConfig
    from .models import Animation

    try:
        animation = Animation.objects.select_related('project').get(pk=animation_id)
    except Animation.DoesNotExist:
        logger.error("Animation %d not found for video assembly.", animation_id)
        return

    # Check ffmpeg availability
    if not WorkersConfig.ffmpeg_detected:
        Animation.objects.filter(pk=animation_id).update(
            video_status='ERROR',
            video_error='FFmpeg is not available on this system.',
        )
        return

    try:
        _assembly_semaphore.acquire()
        try:
            _run_assembly(animation, animation_id)
        finally:
            _assembly_semaphore.release()
    except Exception:
        logger.exception("Unexpected error in video assembly for animation %d.", animation_id)


def _filter_valid_frames(frame_files, animation_id):
    """Filter frame files to only those within MEDIA_ROOT."""
    valid = []
    for idx, path in frame_files:
        if _validate_path_in_media_root(path):
            valid.append((idx, path))
        else:
            logger.warning(
                "Animation %d: frame path %s is outside MEDIA_ROOT, skipping.",
                animation_id, path,
            )
    return valid


def _execute_ffmpeg(cmd, animation_id):
    """
    Run ffmpeg and return (success, error_message) tuple.

    Returns (True, None) on success, (False, error_msg) on failure.
    """
    logger.info("Running ffmpeg for animation %d: %s", animation_id, ' '.join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=3600,
            text=True,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return False, 'FFmpeg timed out after 1 hour.'

    if result.returncode != 0:
        logger.error(
            "FFmpeg failed for animation %d (exit %d): %s",
            animation_id, result.returncode, result.stderr,
        )
        return False, (result.stderr or 'Unknown ffmpeg error')[:2000]

    return True, None


def _run_assembly(animation, animation_id):
    """Core assembly logic, called while holding the semaphore."""
    from .models import Animation

    temp_dir = None
    try:
        render_settings = animation.render_settings or {}
        output_format = render_settings.get(
            RenderSettings.IMAGE_FILE_FORMAT, 'PNG',
        )
        extension = FORMAT_EXTENSIONS.get(output_format, '.png')

        frame_files = _collect_frame_files(animation)
        valid_frames = _filter_valid_frames(frame_files, animation_id)

        if not valid_frames:
            Animation.objects.filter(pk=animation_id).update(
                video_status='ERROR',
                video_error='No valid frame files found for video assembly.',
            )
            return

        temp_dir = tempfile.mkdtemp(prefix='sethlans_video_')
        frame_pattern = _prepare_temp_frames(valid_frames, temp_dir, extension)

        video_settings = animation.video_settings
        container = video_settings['container']
        safe_name = slugify(animation.name) or 'animation'
        output_filename = f"{safe_name}.{container}"
        output_path = os.path.join(temp_dir, output_filename)

        try:
            cmd = build_ffmpeg_command(frame_pattern, output_path, video_settings)
        except ValueError as e:
            Animation.objects.filter(pk=animation_id).update(
                video_status='ERROR',
                video_error=str(e)[:2000],
            )
            return

        success, error_msg = _execute_ffmpeg(cmd, animation_id)
        if not success:
            Animation.objects.filter(pk=animation_id).update(
                video_status='ERROR',
                video_error=error_msg,
            )
            return

        # Save the video file
        with open(output_path, 'rb') as f:
            video_content = f.read()

        animation.video_file.save(
            output_filename, ContentFile(video_content), save=False,
        )
        Animation.objects.filter(pk=animation_id).update(
            video_file=animation.video_file,
            video_status='DONE',
        )
        logger.info("Video assembly complete for animation %d.", animation_id)

    except Exception as e:
        logger.exception(
            "Error during video assembly for animation %d.", animation_id,
        )
        Animation.objects.filter(pk=animation_id).update(
            video_status='ERROR',
            video_error=str(e)[:2000],
        )
    finally:
        if temp_dir and os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
