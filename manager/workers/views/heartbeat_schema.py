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
from ..serializers.worker_card_schemas import CardChoiceRequestField


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
    fields injected by ``_process_heartbeat`` after the base
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
    dismissed_card_alerts = serializers.ListField(
        child=serializers.CharField(),
        help_text=(
            "Ids of card alerts an admin dismissed (worker card selection "
            "FR-CR-4).  The worker removes these alerts; an unknown id is "
            "ignored.  Taken only from this authenticated answer."
        ),
    )

    # Inherited from WorkerSerializer (so not re-added to ``fields``); in
    # this answer it is the value after the heartbeat's outcome was
    # processed (FR-CM-12, D3).
    card_choice_request = CardChoiceRequestField(
        help_text=(
            "The pending graphics card change for this worker (worker card "
            "selection FR-CM-12), null when none.  Carried in every answer "
            "until the worker reports its outcome or an admin withdraws it."
        ),
    )

    class Meta(WorkerSerializer.Meta):
        # Re-declare fields so the new entries appear in the schema in
        # addition to every WorkerSerializer field.  We don't mutate
        # WorkerSerializer.Meta.fields — that would alter the base
        # serializer's output for every other consumer.
        fields = list(WorkerSerializer.Meta.fields) + [
            "required_blender_versions",
            "dismissed_card_alerts",
        ]
