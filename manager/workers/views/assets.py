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
from ..services.row_locks import lock_own_rows
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
        """Lock the asset's jobs (by pk) before deleting it (FR-PM-10 lock order).

        The cascade deletes the asset's measurement records and jobs; with
        the jobs locked first it keeps the ``pilot-result/`` order (Job ->
        UnrealMeasurement).  Assets without measurements keep the plain delete.
        """
        try:
            measured = strategy_for_source_type(instance.source_type).supports('pilot_measurement')
        except UnsupportedRendererError:
            measured = False
        if not measured:
            return super().perform_destroy(instance)
        with transaction.atomic():
            list(lock_own_rows(Job.objects.filter(asset=instance).order_by('pk')).values_list('pk', flat=True))
            instance.delete()

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
