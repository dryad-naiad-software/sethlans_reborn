# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``/api/sequence-submissions/`` (Unreal sequence rendering FR-SQ-4,
FR-SQ-10, FR-SQ-11, FR-SR-3, FR-SL-5): admin only.

``POST`` (body class ``RESULT_BODY_LIMIT``) creates a version: planned at
once when its shots are known (QUEUED), else DISCOVERING until the
discovery finishes.  ``GET`` lists versions newest first, filtered by
``project`` and ``asset``, with progress counts as one correlated subquery
per count (never several ``Count`` over joins) and the routing note from
one subquery per row plus one query per response for the picked jobs
(``services/sequence_routing.py``), so no query runs per row; the detail
adds the shots and the sound runs.  A submission is read-only through the
API: no ``PUT`` or ``PATCH`` (FR-SQ-10).  The actions live in
``sequence_control.py`` (cancel, re-render, delete, retries, merge) and
``sequence_downloads.py`` (FR-SL).
"""

from django.db.models import Count, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view, inline_serializer
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from ..constants import UnrealKind
from ..constants_sequence import ShotStatus
from ..models import Job, JobStatus, SequenceShot, SequenceSubmission
from ..permissions import IsAdmin
from ..serializers.sequence_submission import (
    CREATE_KEYS, SequenceSubmissionCreateSerializer, SequenceSubmissionDetailSerializer,
    SequenceSubmissionSerializer,
)
from ..services.sequence_routing import CONTEXT_KEY, ROUTING_JOB, routing_job_annotation, routing_jobs
from ..services.sequence_submit import create_submission
from ..strict_json import unknown_keys
from .sequence_control import SequenceControlMixin
from .sequence_downloads import SequenceDownloadsMixin

_TAGS = ['Management UI']


class SubmissionPagination(PageNumberPagination):
    """50 versions a page, at most 200 (``?page_size=``)."""

    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


def _count(queryset, link):
    """One correlated subquery counting ``queryset`` rows of the outer submission."""
    counted = queryset.filter(**{link: OuterRef('pk')}).order_by().values(link).annotate(c=Count('pk')).values('c')
    return Coalesce(Subquery(counted[:1], output_field=IntegerField()), Value(0))


def submission_counts():
    """FR-SQ-11 progress counts as subquery annotations."""
    shots = SequenceShot.objects.all()
    pieces = Job.objects.filter(unreal_kind=UnrealKind.RENDER)
    return {
        'shots_total': _count(shots, 'submission'),
        'shots_done': _count(shots.filter(status=ShotStatus.DONE), 'submission'),
        'shots_error': _count(shots.filter(status=ShotStatus.ERROR), 'submission'),
        'pieces_total': _count(pieces, 'sequence_submission'),
        'pieces_done': _count(pieces.filter(status=JobStatus.DONE), 'sequence_submission'),
    }


@extend_schema_view(
    list=extend_schema(tags=_TAGS),
    retrieve=extend_schema(tags=_TAGS),
)
class SequenceSubmissionViewSet(
    SequenceControlMixin, SequenceDownloadsMixin,
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet,
):
    """Versions of Unreal sequence renders (admin only)."""

    queryset = SequenceSubmission.objects.all()
    serializer_class = SequenceSubmissionSerializer
    permission_classes = [IsAdmin]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['project', 'asset']
    pagination_class = SubmissionPagination

    def get_serializer_class(self):
        return SequenceSubmissionDetailSerializer if self.action == 'retrieve' else SequenceSubmissionSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action not in ('list', 'retrieve', 'create'):
            return queryset
        queryset = queryset.annotate(**submission_counts(), **routing_job_annotation())
        if self.action == 'retrieve':
            queryset = queryset.prefetch_related('shots')
        return queryset

    def _with_routing(self, rows):
        """Load the routing notes' jobs of ``rows`` in one query (FR-SR-3)."""
        context = self.get_serializer_context()
        context[CONTEXT_KEY] = routing_jobs(getattr(row, ROUTING_JOB, None) for row in rows)
        return context

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        rows = list(page if page is not None else queryset)
        data = self.get_serializer_class()(rows, many=True, context=self._with_routing(rows)).data
        return self.get_paginated_response(data) if page is not None else Response(data)

    def retrieve(self, request, *args, **kwargs):
        submission = self.get_object()
        return Response(SequenceSubmissionDetailSerializer(submission, context=self._with_routing([submission])).data)

    @extend_schema(
        tags=_TAGS,
        request=SequenceSubmissionCreateSerializer,
        responses={
            201: SequenceSubmissionDetailSerializer,
            400: OpenApiResponse(
                inline_serializer('SequenceSubmissionError', {'detail': serializers.CharField(required=False)}),
                description='{"detail": "Unknown keys: ..."} or a per-field error object.',
            ),
        },
    )
    def create(self, request, *args, **kwargs):
        """Submit a sequence render: a new version (FR-SQ-2, FR-SQ-4)."""
        unknown = unknown_keys(request.data, CREATE_KEYS)
        if unknown:
            return Response({'detail': f"Unknown keys: {', '.join(unknown)}."}, status=status.HTTP_400_BAD_REQUEST)
        body = SequenceSubmissionCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        submission = create_submission(body.validated_data)
        created = self.get_queryset().prefetch_related('shots').get(pk=submission.pk)
        data = SequenceSubmissionDetailSerializer(created, context=self._with_routing([created])).data
        return Response(data, status=status.HTTP_201_CREATED)
