# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Downloads of a sequence version (Unreal sequence rendering FR-SL-1..5,
Q11, D17): admin only, as every submission endpoint.

For the whole version and for one shot (``shots/<index>/...``):

* ``download-video/`` -- the silent video; ``?sound=1`` the video with the
  sound merged in (chunk 6's merged copy);
* ``download-sound/`` -- the ``.wav``;
* ``download-frames/`` -- the frames as a streamed zip (``zip_stream``):
  entries ``<index>_<shot>/<shot>.<frame, 7 digits>.<ext>`` for the whole
  version, ``<shot>.<frame, 7 digits>.<ext>`` for one shot; only DONE
  shots; no frame cap; 429 when every zip slot is busy; 409 when a frame
  file is missing or unreadable (checked before streaming, so a zip is
  never quietly incomplete).

As the original Sethlans did for animations (a frames zip and the video),
with sound and the merged copy added.  File names are
``<project>_<sequence>_v<n>[_<shot>].<ext>`` from slugs (``slugify``), so
no stored path or worker-made name reaches the client.  Every download is
404 "Not available." when its item is not DONE, or its file is missing or
outside ``MEDIA_ROOT``.
"""

import logging
import os

from django.http import FileResponse, StreamingHttpResponse
from django.utils.text import slugify
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from ..constants_sequence import ShotStatus
from ..constants_video import CONTAINER_CONTENT_TYPES, VideoStatus
from ..models import SequenceFrame, SequenceShot, SequenceSubmission
from ..models.upload_paths_sequence import checked_path
from .zip_stream import SLOTS, SlotStream

logger = logging.getLogger(__name__)

NOT_AVAILABLE = "Not available."
ZIP_BUSY = "Too many zip downloads at once; try again shortly."
FRAMES_MISSING = "A frame file of this version is missing or unreadable ({entry}); render its shot again."
FRAME_EXTENSIONS = ('png', 'exr')
_SHOT = r'shots/(?P<index>[0-9]{1,6})'
_TAGS = ['Management UI']
_SOUND = OpenApiParameter('sound', OpenApiTypes.INT, description="1: the video with the sound merged in.")


def _schema(description, parameters=None, zip_download=False):
    responses = {(200, '*/*'): OpenApiResponse(OpenApiTypes.BINARY, description=description),
                 404: OpenApiResponse(description='{"error": "Not available."}')}
    if zip_download:
        responses[409] = OpenApiResponse(description='{"error": "A frame file of this version is missing ..."}')
        responses[429] = OpenApiResponse(description='{"error": "Too many zip downloads at once; ..."}')
    return extend_schema(tags=_TAGS, parameters=parameters or [], responses=responses)


def _not_available():
    return Response({'error': NOT_AVAILABLE}, status=status.HTTP_404_NOT_FOUND)


def _slug(text, fallback):
    return slugify(text or '') or fallback


def shot_slug(name, index):
    """A shot name as it appears in zip entries and file names (FR-SD-6)."""
    return _slug(name, f'shot_{int(index):03d}')


def download_name(submission, extension, shot=None):
    """FR-SL-3: ``<project slug>_<sequence slug>_v<n>[_<shot slug>].<ext>``."""
    sequence = submission.sequence.rsplit('/', 1)[-1].split('.', 1)[0]
    parts = [_slug(submission.project.name, 'project'), _slug(sequence, 'sequence'), f'v{submission.version}']
    if shot is not None:
        parts.append(shot_slug(shot.name, shot.index))
    return f"{'_'.join(parts)}.{extension}"


def stored_path(field_file):
    """The checked absolute path of a stored file, or ``None`` when unset, outside ``MEDIA_ROOT`` or missing."""
    if not field_file or not field_file.name:
        return None
    try:
        path = checked_path(field_file.storage, field_file.name)
    except ValueError:
        logger.warning("Download refused: %s resolves outside MEDIA_ROOT.", field_file.name)
        return None
    return path if os.path.isfile(path) else None


def _content_type(extension):
    return 'audio/wav' if extension == 'wav' else CONTAINER_CONTENT_TYPES.get(extension, 'application/octet-stream')


def _readable(path):
    return os.path.isfile(path) and os.access(path, os.R_OK)


def _entries(rows, whole):
    """``(entries, problem)``: ``(arcname, path)`` of frame rows ``(index, name, frame, file name)``.

    ``problem`` names the first frame that is outside ``MEDIA_ROOT``, missing
    or unreadable; a zip is then refused rather than streamed without it.
    """
    storage = SequenceFrame._meta.get_field('file').storage
    entries = []
    for index, name, frame, file_name in rows:
        extension = os.path.splitext(file_name)[1].lstrip('.').lower()
        slug = shot_slug(name, index)
        entry = f'{slug}.{int(frame):07d}.{extension}'
        try:
            path = checked_path(storage, file_name)
        except ValueError:
            logger.warning("Zip download: frame file %s resolves outside MEDIA_ROOT.", file_name)
            return [], entry
        if extension not in FRAME_EXTENSIONS or not _readable(path):
            return [], entry
        entries.append((f'{int(index):04d}_{slug}/{entry}' if whole else entry, path))
    return entries, None


class SequenceDownloadsMixin:
    """The FR-SL download actions of ``SequenceSubmissionViewSet``."""

    def _version(self, pk):
        if not str(pk).isdigit():
            return None
        return SequenceSubmission.objects.select_related('project').filter(pk=int(pk)).first()

    def _shot(self, submission, index):
        return SequenceShot.objects.filter(submission=submission, index=int(index)).first()

    def _file(self, pk, item, index=None):
        submission = self._version(pk)
        if submission is None:
            return _not_available()
        row = submission if index is None else self._shot(submission, index)
        if row is None or getattr(row, f'{item}_status') != VideoStatus.DONE:
            return _not_available()
        field_file = getattr(row, f'{item}_file')
        path = stored_path(field_file)
        if path is None:
            return _not_available()
        extension = os.path.splitext(field_file.name)[1].lstrip('.').lower()
        name = download_name(submission, extension, None if index is None else row)
        try:
            handle = open(path, 'rb')
        except OSError:
            return _not_available()
        return FileResponse(handle, as_attachment=True, filename=name, content_type=_content_type(extension))

    def _zip(self, pk, index=None):
        submission = self._version(pk)
        if submission is None:
            return _not_available()
        shot = None
        frames = SequenceFrame.objects.filter(shot__submission=submission, shot__status=ShotStatus.DONE)
        if index is not None:
            shot = self._shot(submission, index)
            if shot is None or shot.status != ShotStatus.DONE:
                return _not_available()
            frames = frames.filter(shot=shot)
        rows = frames.order_by('shot__index', 'frame_number').values_list(
            'shot__index', 'shot__name', 'frame_number', 'file')
        entries, problem = _entries(rows, whole=index is None)
        if problem is not None:
            return Response({'error': FRAMES_MISSING.format(entry=problem)}, status=status.HTTP_409_CONFLICT)
        if not entries:
            return _not_available()
        lease = SLOTS.take()
        if lease is None:
            return Response({'error': ZIP_BUSY}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        try:
            response = StreamingHttpResponse(SlotStream(entries, lease), content_type='application/zip')
        except BaseException:
            lease.give_back()
            raise
        response['Content-Disposition'] = f'attachment; filename="{download_name(submission, "zip", shot)}"'
        return response

    @staticmethod
    def _video_item(request):
        return 'sound_video' if request.query_params.get('sound') in ('1', 'true') else 'video'

    @_schema("The version's video (mp4, webm or mov).", [_SOUND])
    @action(detail=True, methods=['get'], url_path='download-video')
    def download_video(self, request, pk=None):
        """The whole version's video; ``?sound=1`` the copy with the sound merged in (FR-SL-1)."""
        return self._file(pk, self._video_item(request))

    @_schema("The version's sound (.wav).")
    @action(detail=True, methods=['get'], url_path='download-sound')
    def download_sound(self, request, pk=None):
        """The whole version's sound as ``.wav`` (FR-SL-1)."""
        return self._file(pk, 'sound')

    @_schema("The frames of the version's DONE shots as a streamed zip.", zip_download=True)
    @action(detail=True, methods=['get'], url_path='download-frames')
    def download_frames(self, request, pk=None):
        """The frames of every DONE shot as a streamed zip (FR-SL-1, FR-SL-2)."""
        return self._zip(pk)

    @_schema("One shot's video.", [_SOUND])
    @action(detail=True, methods=['get'], url_path=f'{_SHOT}/download-video')
    def shot_download_video(self, request, pk=None, index=None):
        """One shot's video; ``?sound=1`` the merged copy (FR-SL-1)."""
        return self._file(pk, self._video_item(request), index)

    @_schema("One shot's sound (.wav).")
    @action(detail=True, methods=['get'], url_path=f'{_SHOT}/download-sound')
    def shot_download_sound(self, request, pk=None, index=None):
        """One shot's sound as ``.wav`` (FR-SL-1)."""
        return self._file(pk, 'sound', index)

    @_schema("One DONE shot's frames as a streamed zip.", zip_download=True)
    @action(detail=True, methods=['get'], url_path=f'{_SHOT}/download-frames')
    def shot_download_frames(self, request, pk=None, index=None):
        """One DONE shot's frames as a streamed zip (FR-SL-1, FR-SL-2)."""
        return self._zip(pk, index)
