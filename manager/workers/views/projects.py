# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
# workers/views/projects.py

import logging

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models import Project
from ..permissions import IsAdmin
from ..serializers import ProjectSerializer

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(tags=['Management UI']),
    retrieve=extend_schema(tags=['Management UI']),
    create=extend_schema(tags=['Management UI']),
    update=extend_schema(tags=['Management UI']),
    partial_update=extend_schema(tags=['Management UI']),
    destroy=extend_schema(tags=['Management UI']),
    pause=extend_schema(tags=['Management UI']),
    unpause=extend_schema(tags=['Management UI']),
)
class ProjectViewSet(viewsets.ModelViewSet):
    """
    API endpoint for creating, retrieving, and managing rendering projects.

    Projects serve as the top-level organizational entity for assets and jobs.
    They can be paused to temporarily stop workers from processing their jobs.
    """
    permission_classes = [IsAdmin]
    queryset = Project.objects.all().order_by('-created_at')
    serializer_class = ProjectSerializer

    @action(detail=True, methods=['post'])
    def pause(self, request, pk=None):
        """
        Pauses a project, preventing workers from picking up any of its jobs.

        Args:
            request: The request object.
            pk: The primary key of the project to pause.

        Returns:
            A Response containing the updated project data.
        """
        project = self.get_object()
        project.is_paused = True
        project.save(update_fields=['is_paused'])
        logger.info(f"Project '{project.name}' (ID: {project.id}) has been paused.")
        return Response(self.get_serializer(project).data)

    @action(detail=True, methods=['post'])
    def unpause(self, request, pk=None):
        """
        Unpauses a project, allowing workers to resume processing its jobs.

        Args:
            request: The request object.
            pk: The primary key of the project to unpause.

        Returns:
            A Response containing the updated project data.
        """
        project = self.get_object()
        project.is_paused = False
        project.save(update_fields=['is_paused'])
        logger.info(f"Project '{project.name}' (ID: {project.id}) has been unpaused.")
        return Response(self.get_serializer(project).data)
