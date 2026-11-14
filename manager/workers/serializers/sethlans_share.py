# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializer for ``GET``/``PUT /api/sethlans-share/`` (Unreal foundation FR-MS-2).
"""

from rest_framework import serializers

from shared.unreal.unc import validate_unc_dir


class SethlansShareSerializer(serializers.Serializer):
    """The manager's Sethlans share root, in canonical UNC form or ``''``.

    The value is not trimmed: the UNC rules reject leading and trailing
    spaces themselves (FR-MU-6), and a silently trimmed value would differ
    from what the admin entered.
    """

    sethlans_share = serializers.CharField(
        max_length=1024, allow_blank=True, trim_whitespace=False,
        help_text=(
            "The farm's Sethlans share root, e.g. \\\\nas.example.lan\\sethlans. "
            "Stored in canonical form (host lowercased). '' clears it."
        ),
    )

    def validate_sethlans_share(self, value):
        """Return the canonical form, or ``''`` to clear the setting."""
        if value == '':
            return ''
        try:
            return validate_unc_dir(value).canonical
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from None


class SethlansShareResponseSerializer(serializers.Serializer):
    """Response of ``GET``/``PUT /api/sethlans-share/`` (OpenAPI only).

    ``warnings`` is present only on a ``PUT`` that stored a value with a
    single-label host, so it is optional in the schema.
    """

    sethlans_share = serializers.CharField(
        help_text="The stored share root in canonical form, or '' when not configured.",
    )
    warnings = serializers.ListField(
        child=serializers.CharField(), required=False,
        help_text="Non-blocking advice about the stored value (PUT only).",
    )
