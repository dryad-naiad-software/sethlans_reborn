# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializers for ``GET /api/ffmpeg-status/``.

Two distinct classes by design — drf-spectacular generates a clean
discriminated-shape OpenAPI schema only when each role-specific shape
is its own serializer with concrete fields.  A single serializer with
a conditional ``to_representation`` would publish a single muddy
schema and break frontend code-gen.
"""

from rest_framework import serializers


class FFmpegDetailsSerializer(serializers.Serializer):
    """The admin-only details block.

    ``source`` is one of: ``"system"`` (PATH or manager.ini override) /
    ``"bundled"`` (downloaded by the manager) / ``""`` while installing.

    ``status`` is one of: ``"installing"`` / ``"ready"`` / ``"failed"``.

    ``error`` is ``null`` unless ``status == "failed"``, in which case
    it is a closed-vocabulary string defined in spec FR §70-79.
    """

    source = serializers.CharField(allow_blank=True)
    version = serializers.CharField(allow_blank=True)
    path = serializers.CharField(allow_blank=True)
    status = serializers.ChoiceField(
        choices=["installing", "ready", "failed"],
    )
    error = serializers.CharField(allow_null=True, required=False)


class FFmpegStatusSerializer(serializers.Serializer):
    """Regular-user payload.  Boolean only — no detail leak."""

    video_assembly_ready = serializers.BooleanField()

    def to_representation(self, instance):
        # ``instance`` is a ``parts_check.registry.Status`` frozen
        # dataclass.  Translate to the boolean shape.
        return {
            "video_assembly_ready": getattr(instance, "status", "")
            == "ready",
        }


class FFmpegStatusAdminSerializer(serializers.Serializer):
    """Admin payload — boolean PLUS the ``ffmpeg`` details block."""

    video_assembly_ready = serializers.BooleanField()
    ffmpeg = FFmpegDetailsSerializer()

    def to_representation(self, instance):
        return {
            "video_assembly_ready": getattr(instance, "status", "")
            == "ready",
            "ffmpeg": {
                "source": getattr(instance, "source", "") or "",
                "version": getattr(instance, "version", "") or "",
                "path": getattr(instance, "path", "") or "",
                "status": getattr(instance, "status", "installing"),
                "error": getattr(instance, "error", None),
            },
        }
