# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frames as ffmpeg input for the sequence builds (Unreal sequence rendering
FR-SV-3, D13), never copied.

A shot's stored frames are named ``<frame, 7 digits>.<ext>`` in their shot
folder, so ffmpeg reads them in place with a ``%07d`` pattern and the shot's
first frame as start number.  Where that does not hold (a renamed file, or
the frames of several shots for the WebM fallback) they are hard-linked,
else symbolic-linked, under one numbered name each into a folder **inside
``MEDIA_ROOT``** (same volume, so a hard link needs no privilege); a frame
that cannot be linked fails the build instead of being copied.  The caller
removes the folder.
"""

import os

from .models import SequenceFrame
from .models.upload_paths_sequence import checked_path


class FrameError(Exception):
    """A frame is missing or cannot be read in place; the message is the manager's own."""


def frame_paths(storage, shot):
    """``[(frame number, absolute path)]`` of every frame of a DONE ``shot``, in order."""
    rows = list(SequenceFrame.objects.filter(shot=shot).order_by('frame_number').values_list('frame_number', 'file'))
    numbers = [number for number, _name in rows]
    if numbers != list(range(shot.start_frame, shot.end_frame)):
        missing = sorted(set(range(shot.start_frame, shot.end_frame)) - set(numbers))
        first = missing[0] if missing else numbers[0]
        raise FrameError(f"Frame {first} of shot {shot.index} is missing.")
    paths = []
    for number, name in rows:
        try:
            path = checked_path(storage, name)
        except ValueError:
            raise FrameError(f"Frame {number} is outside the media folder.") from None
        if not os.path.isfile(path):
            raise FrameError(f"Frame {number} of shot {shot.index} is missing.")
        paths.append((number, path))
    return paths


def _link(source, target):
    try:
        os.link(source, target)
    except OSError:
        try:
            os.symlink(source, target)
        except OSError:
            raise FrameError("The frames could not be linked for ffmpeg (the media folder allows no links).") from None


def frame_pattern(frames, extension, link_folder):
    """``(pattern, start number)`` for ``frames``: in place when their names allow, else links in ``link_folder``."""
    folder = os.path.dirname(frames[0][1])
    in_place = '%' not in folder and all(
        os.path.dirname(path) == folder and os.path.basename(path) == f'{number:07d}.{extension}'
        for number, path in frames)
    if in_place:
        return os.path.join(folder, f'%07d.{extension}'), frames[0][0]
    if '%' in link_folder:
        raise FrameError("The media folder's path cannot be given to ffmpeg as a pattern.")
    os.makedirs(link_folder, exist_ok=True)
    for position, (_number, path) in enumerate(frames, 1):
        _link(path, os.path.join(link_folder, f'{position:07d}.{extension}'))
    return os.path.join(link_folder, f'%07d.{extension}'), 1
