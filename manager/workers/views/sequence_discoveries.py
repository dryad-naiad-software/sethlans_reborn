# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``/api/sequence-discoveries/`` (Unreal sequence rendering FR-SD-2, FR-SD-9,
FR-SL-5): admin only.

``POST`` returns the latest record of the key while it is QUEUED, RUNNING
or DONE (200), else a new record and its ``shots`` job (201).  Reads settle
open records whose job is final first (FR-SD-9), so a crash between a
``shots`` job's failure and its after-commit callback cannot leave a
record waiting for ever.  The list is paginated and shows summaries (no
shots or warnings); it settles only the rows of the page it returns, so
one request takes a bounded number of write locks.
"""

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view, inline_serializer
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from ..constants_sequence import OPEN_DISCOVERY_STATUSES
from ..models import SequenceDiscovery
from ..permissions import IsAdmin
from ..serializers.sequence_discovery import (
    CREATE_KEYS, SequenceDiscoveryCreateSerializer, SequenceDiscoverySerializer,
    SequenceDiscoverySummarySerializer,
)
from ..services.sequence_discovery import request_discovery, settle_discovery, stale_discoveries
from ..strict_json import unknown_keys

_TAGS = ['Management UI']


class DiscoveryPagination(PageNumberPagination):
    """50 records a page, at most 200 (``?page_size=``)."""

    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


def _settle_stale(discoveries):
    """FR-SD-9 for the given records only; refresh each one that was settled."""
    open_ids = [d.pk for d in discoveries if d.status in OPEN_DISCOVERY_STATUSES]
    if not open_ids:
        return
    stale = set(stale_discoveries(SequenceDiscovery.objects.filter(pk__in=open_ids)).values_list('pk', flat=True))
    for discovery in discoveries:
        if discovery.pk in stale and settle_discovery(discovery.pk):
            discovery.refresh_from_db()


@extend_schema_view(
    list=extend_schema(tags=_TAGS),
    retrieve=extend_schema(tags=_TAGS),
)
class SequenceDiscoveryViewSet(
    mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet,
):
    """Shot discovery records of Unreal sequences (admin only)."""

    queryset = SequenceDiscovery.objects.all()
    serializer_class = SequenceDiscoverySerializer
    permission_classes = [IsAdmin]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['project', 'asset']
    pagination_class = DiscoveryPagination

    def get_serializer_class(self):
        return SequenceDiscoverySummarySerializer if self.action == 'list' else SequenceDiscoverySerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.defer('shots', 'warnings') if self.action == 'list' else queryset

    @extend_schema(
        tags=_TAGS,
        request=SequenceDiscoveryCreateSerializer,
        responses={
            200: OpenApiResponse(SequenceDiscoverySerializer, description="The key's latest record, reused."),
            201: OpenApiResponse(SequenceDiscoverySerializer, description="A new record and its shots job."),
            400: OpenApiResponse(
                inline_serializer('SequenceDiscoveryError', {'detail': serializers.CharField(required=False)}),
                description='{"detail": "Unknown keys: ..."} or a per-field error object.',
            ),
        },
    )
    def create(self, request, *args, **kwargs):
        """Start or reuse the reading of a sequence's shots (FR-SD-2)."""
        unknown = unknown_keys(request.data, CREATE_KEYS)
        if unknown:
            return Response({'detail': f"Unknown keys: {', '.join(unknown)}."}, status=status.HTTP_400_BAD_REQUEST)
        body = SequenceDiscoveryCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        discovery, created = request_discovery(data['asset'], data['map'], data['sequence'], data['preset'])
        code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(self.get_serializer(discovery).data, status=code)

    def retrieve(self, request, *args, **kwargs):
        """One record; an open record whose job ended is settled first (FR-SD-9)."""
        discovery = self.get_object()
        _settle_stale([discovery])
        return Response(self.get_serializer(discovery).data)

    def list(self, request, *args, **kwargs):
        """Summaries newest first, filtered by ``project`` and ``asset``; the page's stale rows settled first."""
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        _settle_stale(page)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)
