# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
OpenAPI schema serializers for the worker heartbeat endpoint.

Extracted from ``heartbeat.py`` to keep that module under the 300-line
cap and to give drf-spectacular a concrete shape to introspect for the
heartbeat response (issue #131).

These serializers are documentation-only — they are referenced from
``@extend_schema(responses=...)`` on ``WorkerHeartbeatViewSet.create``
and never instantiated against runtime data.  The actual response is
still assembled as a plain dict in ``_process_heartbeat``; if a new
field is added to that response, mirror it here so it appears in
``/api/schema/``.
"""

from drf_spectacular.utils import inline_serializer
from rest_framework import serializers

from ..serializers import WorkerSerializer


# Item shape for ``required_blender_versions`` — each entry advertises
# both the major series (e.g. ``"4.2"``) and the resolved patch version
# (e.g. ``"4.2.3"``).  Workers use this to decide which Blender builds
# to download/install.
_RequiredBlenderVersionItem = inline_serializer(
    name="RequiredBlenderVersion",
    fields={
        "series": serializers.CharField(
            help_text="Major Blender series, e.g. '4.2'.",
        ),
        "version": serializers.CharField(
            help_text="Resolved patch version, e.g. '4.2.3'.",
        ),
    },
)


class WorkerHeartbeatResponseSerializer(WorkerSerializer):
    """Documents the heartbeat response envelope for OpenAPI.

    Inherits every field on :class:`WorkerSerializer` and adds the
    two fields injected by ``_process_heartbeat`` after the base
    ``WorkerSerializer`` payload is produced.

    .. note::

       The response never carries an API token, on either branch.  The
       worker obtains its token from ``POST /api/enroll/`` and already
       holds it when it calls this endpoint (issue #215 removed the
       former ``token`` field from the full-registration response).
    """

    required_blender_versions = serializers.ListField(
        child=_RequiredBlenderVersionItem,
        help_text=(
            "Blender series/versions the manager wants every worker to "
            "have installed.  Workers compare this list against their "
            "local installations and download missing builds."
        ),
    )
    manager_setup_complete = serializers.BooleanField(
        help_text=(
            "True once the manager's first-run setup wizard has "
            "finished (sentinel file present).  Workers must self-gate "
            "Blender downloads and job-claim attempts on this flag — "
            "see issue #126."
        ),
    )

    class Meta(WorkerSerializer.Meta):
        # Re-declare fields so the new entries appear in the schema in
        # addition to every WorkerSerializer field.  We don't mutate
        # WorkerSerializer.Meta.fields — that would alter the base
        # serializer's output for every other consumer.
        fields = list(WorkerSerializer.Meta.fields) + [
            "required_blender_versions",
            "manager_setup_complete",
        ]
