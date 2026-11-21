# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
What each sequence build task makes (Unreal sequence rendering FR-SV-3..6,
D13, D15, U-8).  ``video_sequence`` claims the item and records the
result; this module only reads rows and writes files.

* Shot video: the shot's frames in frame order, ``-framerate num/den`` and
  the fixed track timescale (``video_ffmpeg.build_sequence_video_command``).
  The frames are read in place when their stored names are the expected
  ``<frame, 7 digits>.png``; otherwise through numbered links in a temporary
  folder.
* Whole video: the shot videos in edit order joined by the concat demuxer
  with stream copy; the list is written into a fresh temporary folder.  Its
  packets are then checked (``video_probe``, U-8): the frame count, and for
  MP4 and MOV a constant frame duration and the exact length; a WebM that
  misses is encoded from every frame instead (D13), any other container
  fails.
* Shot sound: cut from its run's ``.wav`` with the run's applied offset
  (``sound_cut.cut_shot``, D15).  Whole sound: the shot cuts joined.
* Merged copy: one video and one audio stream (``build_mux_command``).

Frames are read in place or through links, never copied
(``video_sequence_frames``).  Each ffmpeg run holds the assembly semaphore
shared with the animation assembler only while it runs, and one build may
take at most :data:`BUILD_SECONDS` in all (the WebM fallback chains four
runs).  Every path is a manager storage path checked under ``MEDIA_ROOT``,
every ffmpeg argument a list element.  A failure raises :class:`BuildError` with
a message the manager wrote; ffmpeg's own output only goes to the log.
"""

import logging
import os
import shutil
import time
import uuid
import wave
from dataclasses import dataclass

from django.core.files import File

from .constants_sequence import FRAME_EXTENSIONS
from .models import Job, SequenceShot, SequenceSubmission
from .models.upload_paths_sequence import checked_path, result_name, submission_dir
from .services import parts_check
from .services.sound_cut import cut_shot, join
from .video_assembler import _assembly_semaphore
from .video_ffmpeg import (
    FFMPEG_TIMEOUT_SECONDS, build_concat_command, build_mux_command, build_probe_command,
    build_sequence_video_command, concat_list_text, run_ffmpeg_output,
)
from .video_probe import FrameProbe, problem
from .video_sequence_frames import FrameError, frame_paths, frame_pattern

logger = logging.getLogger(__name__)

NO_FFMPEG = "FFmpeg is not available on this system."
TOO_LONG = "The build took longer than 1 hour."
# The most one build may take, all its ffmpeg runs together.
BUILD_SECONDS = FFMPEG_TIMEOUT_SECONDS


class BuildError(Exception):
    """A build failed; the message is the manager's own text, safe to store."""


@dataclass
class Build:
    """What a builder works on: the submission, the shot index (``None`` for the whole), the rate, its folders."""

    submission: SequenceSubmission
    index: object
    rate: tuple
    temp: str
    links: str
    deadline: float

    @property
    def subject(self):
        return f"sequence {self.submission.pk} " + ("whole" if self.index is None else f"shot {self.index}")


def storage():
    return SequenceShot._meta.get_field('video_file').storage


def _path(name, what):
    if not name:
        raise BuildError(f"The {what} is missing.")
    try:
        path = checked_path(storage(), name)
    except ValueError:
        raise BuildError(f"The {what} is outside the media folder.") from None
    if not os.path.isfile(path):
        raise BuildError(f"The {what} is missing.")
    return path


def _need_ffmpeg():
    if parts_check.get_status('ffmpeg').status != 'ready':
        raise BuildError(NO_FFMPEG)


def _ffmpeg(ctx, cmd, on_line=None):
    """One ffmpeg run within the build's time left, holding the assembly semaphore only meanwhile."""
    with _assembly_semaphore:
        remaining = ctx.deadline - time.monotonic()
        if remaining <= 0:
            raise BuildError(TOO_LONG)
        code, stderr = run_ffmpeg_output(cmd, ctx.subject, timeout=remaining, on_line=on_line)
    if code is None:
        raise BuildError(stderr)
    if code != 0:
        logger.error("FFmpeg failed for %s (exit %d): %s", ctx.subject, code, stderr[-2000:])
        raise BuildError(f"FFmpeg failed (exit {code}); see the manager log.")


def probe(ctx, path):
    """The packets of a video (U-8)."""
    result = FrameProbe()
    _ffmpeg(ctx, build_probe_command(path), on_line=result.line)
    return result


def _frames(shot):
    try:
        return frame_paths(storage(), shot)
    except FrameError as exc:
        raise BuildError(str(exc)) from None


def _encode(ctx, frames):
    submission = ctx.submission
    container = submission.video_settings['container']
    try:
        pattern, start = frame_pattern(frames, FRAME_EXTENSIONS[submission.frame_format], ctx.links)
    except FrameError as exc:
        raise BuildError(str(exc)) from None
    out = os.path.join(ctx.temp, f'video.{container}')
    _ffmpeg(ctx, build_sequence_video_command(pattern, out, submission.video_settings, ctx.rate, start, len(frames)))
    return out, container


def _shot(submission, index):
    shot = SequenceShot.objects.filter(submission=submission, index=index).first()
    if shot is None:
        raise BuildError(f"Shot {index} is gone.")
    return shot


def shot_video(ctx):
    _need_ffmpeg()
    return _encode(ctx, _frames(_shot(ctx.submission, ctx.index)))


def _ordered_shots(submission):
    return list(SequenceShot.objects.filter(submission=submission).order_by('start_frame', 'index'))


def whole_video(ctx):
    _need_ffmpeg()
    submission = ctx.submission
    shots = _ordered_shots(submission)
    paths = [_path(shot.video_file.name, f"video of shot {shot.index}") for shot in shots]
    container = submission.video_settings['container']
    listing = os.path.join(ctx.temp, 'concat.txt')
    with open(listing, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(concat_list_text(paths))
    out = os.path.join(ctx.temp, f'whole.{container}')
    _ffmpeg(ctx, build_concat_command(listing, out, container, ctx.rate))
    expected = sum(shot.end_frame - shot.start_frame for shot in shots)
    wrong = problem(probe(ctx, out), expected, container, ctx.rate)
    if wrong is None:
        return out, container
    if container != 'webm':
        raise BuildError(wrong)
    logger.warning("Sequence %s: %s Encoding the WebM from the frames.", submission.pk, wrong)
    out, container = _encode(ctx, [row for shot in shots for row in _frames(shot)])
    wrong = problem(probe(ctx, out), expected, container, ctx.rate)
    if wrong is not None:
        raise BuildError(wrong)
    return out, container


def _run_of(submission, shot):
    for run in submission.sound_runs or []:
        if isinstance(run, dict) and run.get('start', 0) <= shot.start_frame and shot.end_frame <= run.get('end', -1):
            return run
    raise BuildError(f"No sound run covers shot {shot.index}.")


def shot_sound(ctx):
    submission, index = ctx.submission, ctx.index
    shot = _shot(submission, index)
    run = _run_of(submission, shot)
    name = Job.objects.filter(pk=run.get('job')).values_list('output_file', flat=True).first()
    source = _path(name, f"sound of the run of shot {index}")
    out = os.path.join(ctx.temp, 'sound.wav')
    try:
        cut_shot(source, out, (shot.start_frame, shot.end_frame), int(run['r0']), ctx.rate,
                 int(run['applied_samples']))
    except (KeyError, TypeError, ValueError, EOFError, wave.Error) as exc:
        raise BuildError(f"The sound of shot {index} could not be cut ({type(exc).__name__}).") from None
    return out, 'wav'


def whole_sound(ctx):
    paths = [_path(shot.sound_file.name, f"sound of shot {shot.index}") for shot in _ordered_shots(ctx.submission)]
    out = os.path.join(ctx.temp, 'sound.wav')
    try:
        join(paths, out)
    except (ValueError, EOFError, wave.Error) as exc:
        raise BuildError(f"The shot sounds could not be joined ({type(exc).__name__}).") from None
    return out, 'wav'


def merged(ctx):
    _need_ffmpeg()
    submission, index = ctx.submission, ctx.index
    row = submission if index is None else _shot(submission, index)
    video = _path(row.video_file.name, "video to merge")
    sound = _path(row.sound_file.name, "sound to merge")
    container = submission.video_settings['container']
    out = os.path.join(ctx.temp, f'merged.{container}')
    _ffmpeg(ctx, build_mux_command(video, sound, out, container, ctx.rate))
    return out, container


_BUILDERS = {
    (False, 'video'): shot_video, (True, 'video'): whole_video,
    (False, 'sound'): shot_sound, (True, 'sound'): whole_sound,
    (False, 'sound_video'): merged, (True, 'sound_video'): merged,
}


def build(key, temp):
    """Build the item ``key`` in ``temp`` and store it; returns the stored name.

    Raises:
        BuildError: the inputs are missing or ffmpeg failed.
    """
    submission_id, index, item = key
    submission = SequenceSubmission.objects.select_related('discovery').filter(pk=submission_id).first()
    if submission is None:
        raise BuildError("The version was deleted.")
    rate = (submission.discovery.frame_rate_num, submission.discovery.frame_rate_den)
    links = checked_path(storage(), f'{submission_dir(submission.project_id, submission.pk)}/links-{uuid.uuid4().hex}')
    ctx = Build(submission, index, rate, temp, links, time.monotonic() + BUILD_SECONDS)
    try:
        path, extension = _BUILDERS[(index is None, item)](ctx)
    finally:
        shutil.rmtree(links, ignore_errors=True)
    name = result_name(submission.project_id, submission.pk, index, item, extension)
    checked_path(storage(), name)
    with open(path, 'rb') as handle:
        stored = storage().save(name, File(handle))
    checked_path(storage(), stored)
    return stored
