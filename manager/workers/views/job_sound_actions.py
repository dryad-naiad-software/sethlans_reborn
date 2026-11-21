# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``POST /api/jobs/{id}/sound-result/`` (Unreal sequence rendering FR-SA-6,
D21, FR-BL-3): an audio job's ``.wav`` and its measured offset.  Worker
tokens only; the worker's own running audio job only.

``multipart/form-data`` with the fields of ``serializers/sequence_sound``
(``file``, ``offset``, ``latency_compensation_skipped``, optional
``render_s`` and ``summary``); any other field is refused.  The ``offset``
object follows ``shared.unreal.sound_result``.  The body class
is ``SOUND_BODY_LIMIT`` (4 GiB + 2 MiB) in Caddy and Waitress, and the
request's upload handlers are replaced by ``TemporaryFileUploadHandler``
before the body is read (``JobSequenceActionsMixin.initialize_request``),
so no ``.wav`` is held in memory.

Check order: the job id (404), ownership (409) and one upload per job at a
time (409) before the body is parsed (a stranger or a retried upload
cannot fill the temporary disk), then the body's fields (400),
the flag (400, it sets the expected length), the file's size (413), its
header and length (400), the ``offset`` object
(400), then the store with ownership checked again under the job lock.
An audio job becomes DONE only here (``services/job_kind_rules.py``).
"""

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from shared.unreal.sound_result import SoundResultError, parse_flag

from ..models import Job
from ..serializers.sequence_sound import (
    SoundResultRequestSerializer, SoundResultResponseSerializer, form_problem, parse_form,
)
from ..services.sequence_frames import FrameRefused
from ..services.sequence_sound_result import check_wav, owned_audio, rate_fraction_of, store_sound, upload_slot
from .job_sequence_actions import _job_pk, _not_found

_ERROR = inline_serializer('SoundResultErrorBody', {'error': serializers.CharField()})


def _refused(exc):
    return Response({'error': exc.message}, status=exc.status)


def _bad(message):
    return Response({'error': message}, status=status.HTTP_400_BAD_REQUEST)


class JobSoundActionsMixin:
    """Adds the worker's ``sound_result`` action to ``JobViewSet``."""

    @extend_schema(
        tags=['Worker Agent'], request={'multipart/form-data': SoundResultRequestSerializer},
        responses={200: SoundResultResponseSerializer, 400: _ERROR, 404: _ERROR, 409: _ERROR, 413: _ERROR},
    )
    @action(detail=True, methods=['post'], url_path='sound-result', parser_classes=[MultiPartParser])
    def sound_result(self, request, pk=None):
        """Store an audio job's ``.wav`` and offset and finish the job (FR-SA-6).

        Returns:
            200 ``{"status": "DONE", "applied_samples": n, "applied_source": s}``;
            400 for the fields, the file or the offset; 404 for an unknown
            job; 409 when the job is not an audio job rendering on this
            worker (a repeat too); 413 for a file over 4 GiB.
        """
        job_pk = _job_pk(pk)
        job = Job.objects.filter(pk=job_pk).first() if job_pk is not None else None
        if job is None:
            return _not_found()
        worker = request.user.worker_profile
        try:
            owned_audio(job, worker)
            with upload_slot(job_pk):
                return self._sound_result(request, job, job_pk, worker)
        except FrameRefused as exc:
            return _refused(exc)

    def _sound_result(self, request, job, job_pk, worker):
        problem = form_problem(request.data)
        if problem:
            return _bad(problem)
        rate_fraction = rate_fraction_of(job)
        if rate_fraction is None:
            return _refused(FrameRefused(409, "The audio job's sequence rate is unknown."))
        file_obj = request.data['file']
        try:
            skipped = parse_flag(request.data['latency_compensation_skipped'])
            wav = check_wav(file_obj, job, rate_fraction, latency_skipped=skipped)
            body = parse_form(request.data, wav[0])
            payload = store_sound(job_pk, worker, file_obj, body, wav)
        except SoundResultError as exc:
            return _bad(str(exc))
        except FrameRefused as exc:
            return _refused(exc)
        except Job.DoesNotExist:
            return _not_found()
        return Response(payload, status=status.HTTP_200_OK)
