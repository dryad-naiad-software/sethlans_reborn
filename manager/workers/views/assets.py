# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
# workers/views/assets.py

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.parsers import FileUploadParser, MultiPartParser

from ..models import Asset
from ..permissions import IsAdminOrWorkerReadOnly
from ..serializers import AssetSerializer


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
