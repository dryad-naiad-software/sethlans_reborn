# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import logging

from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.parsers import FileUploadParser, MultiPartParser

from ..models import Asset, Job
from ..renderers import UnsupportedRendererError, strategy_for_source_type
from ..services.delete_locks import lock_for_delete
from ..permissions import IsAdminOrWorkerReadOnly
from ..serializers import AssetSerializer

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(tags=['Management UI']),
    retrieve=extend_schema(tags=['Management UI']),
    create=extend_schema(tags=['Management UI']),
    update=extend_schema(tags=['Management UI']),
    partial_update=extend_schema(tags=['Management UI']),
    destroy=extend_schema(tags=['Management UI']),
)
class AssetViewSet(viewsets.ModelViewSet):
    """
    API endpoint for uploading and managing .blend file assets.
    Assets are uploaded as multipart/form-data.
    """
    permission_classes = [IsAdminOrWorkerReadOnly]
    queryset = Asset.objects.all()
    serializer_class = AssetSerializer
    parser_classes = (MultiPartParser, FileUploadParser)
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['project']

    def perform_destroy(self, instance):
        """Take the delete's locks first, in D15 order, then delete (FR-PM-10, issue #288).

        The cascade deletes the asset's measurement records, jobs and
        sequence rows.  For a renderer with measurement records: the
        project's row, the asset's Job rows, the Worker rows of its records,
        its records, discoveries and submissions, each by pk
        (``delete_locks.lock_for_delete``), before Django's delete collector
        runs.  An asset whose project was deleted meanwhile deletes nothing.
        Assets without measurements keep the plain delete.
        """
        try:
            strategy = strategy_for_source_type(instance.source_type)
        except UnsupportedRendererError:
            strategy = None
        if strategy is None or not strategy.supports('pilot_measurement'):
            return super().perform_destroy(instance)
        with transaction.atomic():
            # ``project_id``, never the lazy ``instance.project``: a project
            # deleted meanwhile is found under the lock (``lock_for_delete``).
            if lock_for_delete(strategy, instance.project_id, Job.objects.filter(asset=instance), asset=instance):
                instance.delete()
        return None

    def create(self, request, *args, **kwargs):
        logger.info(
            "Asset upload request: data_keys=%s, files_keys=%s, "
            "content_type=%s",
            list(request.data.keys()),
            list(request.FILES.keys()),
            request.content_type,
        )
        for key, value in request.data.items():
            if hasattr(value, 'name'):
                logger.info("  %s: file=%s size=%s", key, value.name, value.size)
            else:
                logger.info("  %s: %r", key, value)
        response = super().create(request, *args, **kwargs)
        if response.status_code >= 400:
            logger.warning("Asset upload failed: %s %s", response.status_code, response.data)
        return response
