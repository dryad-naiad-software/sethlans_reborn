# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Strict DRF fields for the self-reported worker bodies (``pilot-result/``,
the sequence results, the measurement retry).

They accept only real JSON types: an ``int`` that is not a ``bool``, a
finite number that is not a string, a real ``bool``, a real string.
``serializers.unreal_measurement`` re-exports them under their old import
path.
"""

from rest_framework import serializers

from ..strict_json import _int_in, is_number


class StrictIntegerField(serializers.IntegerField):
    """An ``int`` (never ``bool``, a string or a float) in ``[min_value, max_value]``."""

    def to_internal_value(self, data):
        if not _int_in(data, self.min_value, self.max_value):
            raise serializers.ValidationError(
                f"Must be an integer between {self.min_value} and {self.max_value}.")
        return data


class StrictNumberField(serializers.FloatField):
    """An ``int`` or a finite ``float`` (never ``bool`` or a string) in range."""

    def to_internal_value(self, data):
        if not is_number(data) or not self.min_value <= data <= self.max_value:
            raise serializers.ValidationError(
                f"Must be a finite number between {self.min_value} and {self.max_value}.")
        return data


class StrictCharField(serializers.CharField):
    """A JSON string only (DRF's ``CharField`` would turn ``5`` into ``"5"``)."""

    def __init__(self, **kwargs):
        kwargs.setdefault('trim_whitespace', False)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        if not isinstance(data, str):
            raise serializers.ValidationError("Must be a string.")
        return super().to_internal_value(data)


class StrictBooleanField(serializers.BooleanField):
    """A JSON ``true`` or ``false`` only."""

    def to_internal_value(self, data):
        if not isinstance(data, bool):
            raise serializers.ValidationError("Must be a boolean.")
        return data
