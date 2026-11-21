# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Storage paths of Unreal sequence results (spec FR-SV-1).

``sequences/<project id>/<submission id>/`` holds ``shots/<index, 4 digits>/``
per shot (frames as ``<frame, 7 digits>.<ext>``, later the shot's video and
sound), ``whole/`` for the whole-submission files and ``runs/`` for the
audio jobs' ``.wav`` files.  Folders are named by shot index, never by shot
name (FR-SD-6).  Every name is made by the manager; :func:`checked_path`
refuses one that would resolve outside ``MEDIA_ROOT``.
"""

import os

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation


def submission_dir(project_id, submission_id):
    return f'sequences/{project_id}/{submission_id}'


def shot_dir(project_id, submission_id, index):
    return f'{submission_dir(project_id, submission_id)}/shots/{int(index):04d}'


def frame_name(project_id, submission_id, index, frame_number, extension):
    """``.../shots/<index>/<frame, 7 digits>.<extension>`` (FR-SV-1)."""
    return f'{shot_dir(project_id, submission_id, index)}/{int(frame_number):07d}.{extension}'


def run_sound_name(project_id, submission_id, job_id):
    """``.../runs/<job id>.wav``: an audio job's rendered run (FR-SA-6), never the worker's name."""
    return f'{submission_dir(project_id, submission_id)}/runs/{int(job_id)}.wav'


# Built results (FR-SV-3..6): the file stem of each item; the extension is a container or ``wav``.
RESULT_STEMS = {'video': 'video', 'sound': 'sound', 'sound_video': 'video_sound'}
RESULT_EXTENSIONS = frozenset({'mp4', 'webm', 'mov', 'wav'})


def result_name(project_id, submission_id, index, item, extension):
    """``.../shots/<index>/<stem>.<ext>`` for a shot (``index``), ``.../whole/<stem>.<ext>`` for ``None``."""
    if item not in RESULT_STEMS or extension not in RESULT_EXTENSIONS:
        raise ValueError(f"Unknown result {item!r}.{extension!r}.")
    folder = (f'{submission_dir(project_id, submission_id)}/whole' if index is None
              else shot_dir(project_id, submission_id, index))
    return f'{folder}/{RESULT_STEMS[item]}.{extension}'


def _basename(filename):
    return os.path.basename(str(filename)) or 'file'


def frame_upload_path(instance, filename):
    """``upload_to`` of ``SequenceFrame.file`` (the service passes its own name)."""
    shot = instance.shot
    return f'{shot_dir(shot.submission.project_id, shot.submission_id, shot.index)}/{_basename(filename)}'


def shot_file_upload_path(instance, filename):
    """``upload_to`` of a ``SequenceShot`` result file."""
    submission = instance.submission
    return f'{shot_dir(submission.project_id, submission.pk, instance.index)}/{_basename(filename)}'


def submission_file_upload_path(instance, filename):
    """``upload_to`` of a whole-submission result file."""
    return f'{submission_dir(instance.project_id, instance.pk)}/whole/{_basename(filename)}'


def checked_path(storage, name):
    """The absolute path of ``name`` in ``storage``, checked to lie under ``MEDIA_ROOT``.

    Raises:
        ValueError: the path resolves outside ``MEDIA_ROOT``.
    """
    root = os.path.realpath(settings.MEDIA_ROOT)
    try:
        path = os.path.realpath(storage.path(name))
    except SuspiciousFileOperation:
        raise ValueError(f"Storage path outside MEDIA_ROOT: {name!r}") from None
    if os.path.commonpath([root, path]) != root:
        raise ValueError(f"Storage path outside MEDIA_ROOT: {name!r}")
    return path
