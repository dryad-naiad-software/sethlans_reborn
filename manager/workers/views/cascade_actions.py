# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Cascade pause / unpause / requeue actions for parent viewsets.

Shared by ``TiledJobViewSet`` and ``AnimationViewSet``: each action
applies to every child ``Job`` of the parent.  The viewset names the
child FK in ``cascade_child_field`` and the parent refresh to run after
a requeue in ``cascade_dispatch`` (issue #219: ``QuerySet.update()``
bypasses ``post_save``, so the parent must be refreshed explicitly).
"""

import logging

from django.db import transaction
from rest_framework.decorators import action
from rest_framework.response import Response

from ..constants import UnrealKind
from ..models import Job, JobStatus

logger = logging.getLogger(__name__)


class CascadeChildActionsMixin:
    """Mixin adding cascade pause/unpause/requeue actions."""

    #: Name of the ``Job`` FK pointing at this viewset's model.
    cascade_child_field = None

    def cascade_dispatch(self, parent_pk):
        """Refresh the parent after a cascade requeue (override)."""
        raise NotImplementedError

    def _children(self, parent):
        return Job.objects.filter(**{self.cascade_child_field: parent})

    def _log_cascade(self, verb, count, parent):
        logger.info(
            f"Cascade {verb} {count} child jobs for "
            f"{type(parent).__name__} '{parent.name}' (ID: {parent.pk})."
        )

    @action(detail=True, methods=['post'])
    def pause(self, request, pk=None):
        """Cascade pause: pause all QUEUED child jobs."""
        parent = self.get_object()
        with transaction.atomic():
            count = self._children(parent).filter(
                status=JobStatus.QUEUED, is_paused=False,
            ).update(is_paused=True)
        self._log_cascade('paused', count, parent)
        return Response({"paused": count})

    @action(detail=True, methods=['post'])
    def unpause(self, request, pk=None):
        """Cascade unpause: unpause all paused QUEUED child jobs."""
        parent = self.get_object()
        with transaction.atomic():
            count = self._children(parent).filter(
                status=JobStatus.QUEUED, is_paused=True,
            ).update(is_paused=False)
        self._log_cascade('unpaused', count, parent)
        return Response({"unpaused": count})

    @action(detail=True, methods=['post'])
    def requeue(self, request, pk=None):
        """Cascade requeue: requeue all ERROR/CANCELED child jobs.

        Always refreshes the parent afterwards, even when nothing was
        requeued, so a parent left out of date (e.g. RENDERING with every
        child DONE) catches up: it can be assembled or completed here.
        """
        parent = self.get_object()
        with transaction.atomic():
            # Pilots are never requeued: the measurement retry replaces them (FR-PM-9).
            count = self._children(parent).filter(
                status__in=[JobStatus.ERROR, JobStatus.CANCELED],
            ).exclude(unreal_kind=UnrealKind.PILOT).update(
                status=JobStatus.QUEUED,
                assigned_worker=None,
                started_at=None,
                completed_at=None,
                error_message='',
                last_output='',
                auto_requeue_count=0,
                is_paused=False,
            )
            self.cascade_dispatch(parent.pk)
        self._log_cascade('requeued', count, parent)
        return Response({"requeued": count})
