# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker result endpoints of Unreal sequence rendering (spec FR-SD-8,
FR-SS-2b, FR-SS-4, D12, D21).  Worker tokens only.

* ``POST /api/jobs/{id}/shots-result/`` (body class ``RESULT_BODY_LIMIT``):
  a ``shots`` job's discovery result.
* ``POST /api/jobs/{id}/frames/`` (multipart ``frame``, ``file``; body class
  ``FRAME_BODY_LIMIT``): one kept frame of a piece.  The request's upload
  handlers are replaced by ``TemporaryFileUploadHandler`` before the body
  is read, so no frame is held in memory.
* ``POST /api/jobs/{id}/piece-result/`` (body class ``RESULT_BODY_LIMIT``):
  a piece's timings; the piece becomes DONE once every frame is stored.

The check order follows ``pilot-result/``: the body's shape (400, before
any database access), the job id (404), then ownership and the job's
rules under the job lock (409, or 400 for the job-dependent rules).  A
``shots`` job and a piece become DONE only here: a PATCH or PUT to DONE
is refused for every caller (``services/job_kind_rules.py``).
"""

from django.core.files.uploadhandler import TemporaryFileUploadHandler
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from shared.unreal.shots_result import FACT_KEYS, TOP_KEYS

from ..models import Job
from ..serializers.sequence_results import (
    FrameUploadRequestSerializer, FrameUploadResponseSerializer, PieceGpuSerializer,
    PieceResultRequestSerializer, PieceResultResponseSerializer, PieceShotSerializer,
    ShotsResultRequestSerializer, ShotsResultResponseSerializer,
)
from ..services.sequence_frames import FrameRefused, owned_piece, store_frame
from ..services.sequence_pieces import record_piece_result
from ..services.sequence_shots_result import record_shots_result
from ..strict_json import unknown_keys

_ERROR = inline_serializer('SequenceResultError', {'error': serializers.CharField()})
_VALIDATION = OpenApiResponse(
    response=inline_serializer('SequenceResultValidationError', {'detail': serializers.CharField(required=False)}),
    description='{"detail": "Unknown keys: ..."}, or {"<field>": ["<message>"]}.',
)
# Actions whose multipart body must go to temporary files (D21, FR-BL-3); ``sound_result``
# is ``job_sound_actions``.
TEMP_FILE_ACTIONS = frozenset({'frames', 'sound_result'})


def _job_pk(pk):
    """The job id as an int, or ``None`` when it is not a plain number."""
    text = str(pk)
    return int(text) if text.isascii() and text.isdigit() and len(text) <= 18 else None


def _frame_number(value):
    """A multipart ``frame`` field: plain digits only (at most 8), else ``None``."""
    text = value if isinstance(value, str) else ''
    return int(text) if text.isascii() and text.isdigit() and len(text) <= 8 else None


def _unknown(data, allowed, **kwargs):
    unknown = unknown_keys(data, allowed, **kwargs)
    if unknown:
        return Response({'detail': f"Unknown keys: {', '.join(unknown)}."}, status=status.HTTP_400_BAD_REQUEST)
    return None


def _not_found():
    return Response({'error': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)


class JobSequenceActionsMixin:
    """Adds the worker's ``shots_result``, ``frames`` and ``piece_result`` actions to ``JobViewSet``."""

    def initialize_request(self, request, *args, **kwargs):
        """Frame and sound uploads go to temporary files only, set before anything reads the body (FR-BL-3)."""
        drf_request = super().initialize_request(request, *args, **kwargs)
        if getattr(self, 'action', None) in TEMP_FILE_ACTIONS:
            request.upload_handlers = [TemporaryFileUploadHandler(request)]
        return drf_request

    @extend_schema(
        tags=['Worker Agent'], request=ShotsResultRequestSerializer,
        responses={200: ShotsResultResponseSerializer, 400: _VALIDATION, 404: _ERROR, 409: _ERROR},
    )
    @action(detail=True, methods=['post'], url_path='shots-result')
    def shots_result(self, request, pk=None):
        """Store a ``shots`` job's discovery result and finish the job (FR-SD-8).

        Returns:
            200 ``{"status": "DONE", "discovery": id, "shots": n}``; 400 for
            the body; 404 for an unknown job; 409 when the job is not
            rendering on this worker, is not a shots job, or its discovery
            no longer waits.  A repeat gets 409.
        """
        refused = _unknown(request.data, set(TOP_KEYS), nested={'preset_facts': set(FACT_KEYS)})
        if refused:
            return refused
        body = ShotsResultRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        job_pk = _job_pk(pk)
        if job_pk is None:
            return _not_found()
        try:
            code, payload = record_shots_result(job_pk, request.user.worker_profile, body.validated_data)
        except Job.DoesNotExist:
            return _not_found()
        return Response(payload, status=code)

    @extend_schema(
        tags=['Worker Agent'], request={'multipart/form-data': FrameUploadRequestSerializer},
        responses={200: FrameUploadResponseSerializer, 400: _ERROR, 404: _ERROR, 409: _ERROR, 413: _ERROR},
    )
    @action(detail=True, methods=['post'], url_path='frames', parser_classes=[MultiPartParser])
    def frames(self, request, pk=None):
        """Store one kept frame of a piece (FR-SS-2b); idempotent per (shot, frame).

        Returns:
            200 ``{"frame": n, "shot": index, "replaced": bool}``; 400 for a
            frame outside the kept range or the piece's shots, or a bad
            file; 404 for an unknown job; 409 when the job is not a piece
            rendering on this worker; 413 for a file over 512 MiB.
        """
        job_pk = _job_pk(pk)
        if job_pk is None:
            return _not_found()
        # Ownership before the body is read (up to 514 MiB of temp disk); checked again under the lock.
        job = Job.objects.filter(pk=job_pk).only('pk', 'unreal_kind', 'sequence_submission', 'status',
                                                 'assigned_worker').first()
        if job is None:
            return _not_found()
        try:
            owned_piece(job, request.user.worker_profile)
        except FrameRefused as exc:
            return Response({'error': exc.message}, status=exc.status)
        frame = _frame_number(request.data.get('frame'))
        file_obj = request.data.get('file')
        if frame is None or not hasattr(file_obj, 'size'):
            return Response({'error': "Send 'frame' (an integer) and 'file'."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            payload = store_frame(job_pk, request.user.worker_profile, frame, file_obj)
        except Job.DoesNotExist:
            return _not_found()
        except FrameRefused as exc:
            return Response({'error': exc.message}, status=exc.status)
        return Response(payload, status=status.HTTP_200_OK)

    @extend_schema(
        tags=['Worker Agent'], request=PieceResultRequestSerializer,
        responses={200: PieceResultResponseSerializer, 400: _VALIDATION, 404: _ERROR, 409: _ERROR},
    )
    @action(detail=True, methods=['post'], url_path='piece-result')
    def piece_result(self, request, pk=None):
        """Finish a piece whose frames are all stored and record its timings (FR-SS-4).

        Returns:
            200 ``{"status": "DONE", "frames": n}``; 400 for the body or
            shots that do not match the piece; 404 for an unknown job; 409
            when the job is not a piece rendering on this worker, or a
            frame is missing (named).  A repeat gets 409.
        """
        refused = _unknown(
            request.data, set(PieceResultRequestSerializer().fields),
            nested={'gpu': set(PieceGpuSerializer().fields)}, lists={'shots': set(PieceShotSerializer().fields)},
        )
        if refused:
            return refused
        body = PieceResultRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        job_pk = _job_pk(pk)
        if job_pk is None:
            return _not_found()
        try:
            code, payload = record_piece_result(job_pk, request.user.worker_profile, body.validated_data)
        except Job.DoesNotExist:
            return _not_found()
        return Response(payload, status=code)
