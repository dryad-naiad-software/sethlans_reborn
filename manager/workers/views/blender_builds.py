# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Cached Blender builds for enrolled workers (BS-MGR-9 .. BS-MGR-13).

``GET /api/blender-builds/<version>/<platform>/`` answers:

- READY with its file present: 200 ``{version, platform, file_name, sha256, size}``;
- FAILED, retry time not come: 404 ``{"status": "failed"}``;
- QUEUED, FAILED-due or FETCHING while this process's runner thread is
  alive: 202 ``{"status": "queued" | "fetching"}``;
- otherwise (not wanted, or no live runner): 404 ``{"status": "not_wanted"}``.

A wanted build without a row gets its QUEUED row here (only that row; no
full reconcile). A READY row whose file is missing or of another size goes
back to QUEUED by a conditional update and is answered as such; when that
update changes nothing, the row is read again and answered as it is now
(gone: ``not_wanted``).

``GET .../archive/`` streams a READY file (``FileResponse``, which Waitress
sends through ``wsgi.file_wrapper``), else 404 ``{"status": "not_ready"}``.
Both need a worker token
(``TokenAuthentication`` + ``IsWorker``). The routes' path converters
accept only a full version and the six platform ids; the file path is built
from the row and checked to stay under the cache root.
"""

from django.http import FileResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.authentication import TokenAuthentication
from rest_framework.response import Response
from rest_framework.views import APIView

from ..constants import BlenderBuildStatus
from ..models import BlenderBuild
from ..permissions import IsWorker
from ..services.blender_cache import paths, runner, wanted

_PATH_PARAMETERS = [
    OpenApiParameter('version', OpenApiTypes.STR, OpenApiParameter.PATH,
                     description="Full Blender version, X.Y.Z (one to three digits each)."),
    OpenApiParameter('platform', OpenApiTypes.STR, OpenApiParameter.PATH,
                     description="Platform id: windows-x64, windows-arm64, linux-x64, linux-arm64, "
                                 "macos-x64 or macos-arm64."),
]
_STATUS_SCHEMA = inline_serializer(
    name='BlenderBuildState',
    fields={'status': serializers.CharField(
        help_text="202: 'queued' or 'fetching' (ask again later). 404: 'failed' or 'not_wanted' "
                  "(fetch the build elsewhere); the archive route answers 404 'not_ready' when "
                  "no READY build with its file exists.")},
)
_METADATA_SCHEMA = inline_serializer(
    name='BlenderBuildMetadata',
    fields={
        'version': serializers.CharField(),
        'platform': serializers.CharField(),
        'file_name': serializers.CharField(),
        'sha256': serializers.CharField(help_text="Official SHA-256 of the archive (64 hex)."),
        'size': serializers.IntegerField(help_text="Archive size in bytes."),
    },
)


def _state(value):
    code = status.HTTP_202_ACCEPTED if value in ('queued', 'fetching') else status.HTTP_404_NOT_FOUND
    return Response({'status': value}, status=code)


def _row(version, platform):
    return BlenderBuild.objects.filter(version=version, platform=platform).first()


def _ready_answer(row):
    """The answer for a READY row: 200 with its file, else the state after re-queuing it (BS-MGR-9)."""
    if paths.ready_file(row) is not None:
        return Response({
            'version': row.version, 'platform': row.platform, 'file_name': row.file_name,
            'sha256': row.sha256, 'size': row.size,
        })
    if wanted.requeue(row.pk, BlenderBuildStatus.READY):
        runner.wake()
        row.status = BlenderBuildStatus.QUEUED
        return _pending_answer(row)
    # The row changed meanwhile (claimed by a fetch, deleted by a reconcile):
    # answer from what it is now.
    fresh = BlenderBuild.objects.filter(pk=row.pk).first()
    return _state('not_wanted') if fresh is None else _pending_answer(fresh)


def _pending_answer(row):
    """The answer for a row that is not READY with its file."""
    failed = row.status == BlenderBuildStatus.FAILED
    if failed and row.next_try_at is not None and row.next_try_at > timezone.now():
        return _state('failed')
    if not runner.is_alive():
        return _state('not_wanted')
    return _state('fetching' if row.status == BlenderBuildStatus.FETCHING else 'queued')


class BlenderBuildView(APIView):
    """Metadata of one cached Blender build, for enrolled workers."""

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsWorker]

    @extend_schema(
        tags=['Worker Agent'],
        parameters=_PATH_PARAMETERS,
        responses={
            200: _METADATA_SCHEMA,
            202: OpenApiResponse(_STATUS_SCHEMA, description="The manager is fetching the build."),
            404: OpenApiResponse(_STATUS_SCHEMA, description="The manager cannot supply the build."),
        },
    )
    def get(self, request, version, platform):
        row = _row(version, platform)
        if row is None:
            if not wanted.ensure_row(version, platform):
                return _state('not_wanted')
            runner.wake()
            row = _row(version, platform)
            if row is None:  # deleted meanwhile by a reconcile
                return _state('not_wanted')
        if row.status == BlenderBuildStatus.READY:
            return _ready_answer(row)
        return _pending_answer(row)


class BlenderBuildArchiveView(APIView):
    """The archive of one READY cached Blender build, for enrolled workers."""

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsWorker]

    @extend_schema(
        tags=['Worker Agent'],
        parameters=_PATH_PARAMETERS,
        responses={
            (200, 'application/octet-stream'): OpenApiTypes.BINARY,
            404: OpenApiResponse(_STATUS_SCHEMA, description="No READY build with its file: "
                                                             "{\"status\": \"not_ready\"}."),
        },
    )
    def get(self, request, version, platform):
        row = _row(version, platform)
        path = paths.ready_file(row) if row is not None and row.status == BlenderBuildStatus.READY else None
        if path is None:
            return Response({'status': 'not_ready'}, status=status.HTTP_404_NOT_FOUND)
        try:
            handle = open(path, 'rb')
        except OSError:
            return Response({'status': 'not_ready'}, status=status.HTTP_404_NOT_FOUND)
        return FileResponse(handle, as_attachment=True, filename=row.file_name,
                            content_type='application/octet-stream')
