# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Fields of the per-machine memory results a worker reports (Unreal
per-machine memory test D1, D3, FR-MT-V-3, FR-MT-S-3).

* :class:`CardKindField` -- a D1 card kind string (``KIND_RE``).
* :class:`DriverVersionField` -- the ``internal:`` driver version of UE's
  ``Driver Version`` log line: ``null`` or ``[0-9A-Za-z.\\-]{1,40}``.  UE's
  literal ``Unknown`` (its value when the lookup fails) becomes ``None``,
  like ``null``: a driver that is not known is never compared (D3).

``pilot-result/`` uses both; ``memory-report/`` takes the same rules.
"""

import re

from rest_framework import serializers
from rest_framework.fields import empty

from shared.unreal.card_kind import is_card_kind

from .strict_fields import StrictCharField

DRIVER_VERSION_RE = re.compile(r'[0-9A-Za-z.\-]{1,40}', re.ASCII)
UNKNOWN_DRIVER = 'Unknown'


class CardKindField(StrictCharField):
    """A card kind ``vvvv:dddd:rrrr`` (lower-case hex, revision ``none`` when unknown)."""

    def __init__(self, **kwargs):
        kwargs.setdefault('max_length', 14)
        kwargs.setdefault('help_text', "The card kind vvvv:dddd:rrrr (PCI vendor, device, revision or none).")
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not is_card_kind(value):
            raise serializers.ValidationError("Must be a card kind vvvv:dddd:rrrr (lower-case hex, revision or none).")
        return value


class DriverVersionField(StrictCharField):
    """``null`` or a driver version ``[0-9A-Za-z.\\-]{1,40}``; ``Unknown`` is returned as ``None``."""

    def __init__(self, **kwargs):
        kwargs.setdefault('allow_null', True)
        kwargs.setdefault('max_length', 40)
        kwargs.setdefault('help_text', "The chosen adapter's internal driver version from the Unreal log; "
                                       "null when unknown (Unreal's 'Unknown' counts as null).")
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not DRIVER_VERSION_RE.fullmatch(value):
            raise serializers.ValidationError("Must be null or 1-40 of A-Z a-z 0-9 . -")
        return value

    def run_validation(self, data=empty):
        # After the validators (``max_length`` cannot measure ``None``): UE's ``Unknown`` is null.
        value = super().run_validation(data)
        return None if value == UNKNOWN_DRIVER else value
