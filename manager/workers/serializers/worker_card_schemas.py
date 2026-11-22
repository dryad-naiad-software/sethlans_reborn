# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
OpenAPI shapes and fields of the worker's graphics card report (worker
card selection FR-CR-1, FR-CR-5, FR-CR-7).

Every inner key is optional (``required=False``): the stored value is
whatever ``views.heartbeat_cards`` kept of a self-reported report, and an
absent or invalid top-level value is left out.  ``card_status`` is
``null`` when the worker never sent a usable report (D3, R3).  The card
change from the manager (FR-CM-14) adds the pending request, the last
outcome (each ``null`` when empty), the action's body and its 202 answer;
the issued ids are never exposed.
"""

from drf_spectacular.utils import extend_schema_field, inline_serializer
from rest_framework import serializers

from ..constants import Renderer

# The value sets of the report (FR-CR-1, FR-CR-3).  ``views.heartbeat_cards``
# validates against them and ``ENUM_NAME_OVERRIDES`` pins their enum names.
CARD_METHODS = ('cpu', 'gpu', 'cpu_gpu')
CARD_GPU_MODES = ('split', 'combined')
# ``unknown``: a ticked card missing after a failed OS probe (C-24a).
CARD_STATES = ('ok', 'lost', 'changed', 'blender_lost', 'held', 'unticked', 'unknown')
CARD_BACKENDS = ('CUDA', 'OPTIX', 'HIP', 'ONEAPI', 'METAL')
CARD_ALERT_KINDS = ('card_lost', 'card_changed', 'card_lost_blender', 'adapter_mismatch')
CARD_NOTICE_KINDS = ('card_new', 'card_gone')

_CARD_SCHEMA = inline_serializer('WorkerCard', {
    'key': serializers.CharField(required=False),
    'name': serializers.CharField(required=False),
    'vendor_id': serializers.IntegerField(required=False, allow_null=True),
    'device_id': serializers.IntegerField(required=False, allow_null=True),
    # PCI revision (Unreal per-machine memory test FR-MT-K-2): ``null`` when
    # not read or invalid.
    'revision_id': serializers.IntegerField(required=False, allow_null=True),
    'ticked': serializers.BooleanField(required=False),
    'state': serializers.ChoiceField(required=False, choices=CARD_STATES),
    'blender': serializers.BooleanField(required=False),
    'backend': serializers.ChoiceField(required=False, allow_null=True, choices=CARD_BACKENDS),
    'note': serializers.CharField(required=False),
    'screen': serializers.BooleanField(required=False, allow_null=True),
    'primary_screen': serializers.BooleanField(required=False, allow_null=True),
    # The job holding the card (Unreal render per card FR-PC-MGR-3): both
    # set, or both ``null`` when the card is free or a value was invalid.
    'job_id': serializers.IntegerField(required=False, allow_null=True),
    'renderer': serializers.ChoiceField(required=False, allow_null=True, choices=Renderer.choices),
})
_ALERT_SCHEMA = inline_serializer('WorkerCardAlert', {
    'id': serializers.CharField(required=False),
    'kind': serializers.ChoiceField(required=False, choices=CARD_ALERT_KINDS),
    'key': serializers.CharField(required=False),
    'name': serializers.CharField(required=False),
    'detail': serializers.CharField(required=False),
    'raised_at': serializers.DateTimeField(required=False, allow_null=True),
})
_NOTICE_SCHEMA = inline_serializer('WorkerCardNotice', {
    'kind': serializers.ChoiceField(required=False, choices=CARD_NOTICE_KINDS),
    'key': serializers.CharField(required=False),
    'name': serializers.CharField(required=False),
})
_WARNING_SCHEMA = inline_serializer('WorkerCardWarning', {
    'code': serializers.CharField(required=False),
    'key': serializers.CharField(required=False, allow_null=True),
    'text': serializers.CharField(required=False),
})
_CARD_STATUS_SCHEMA = inline_serializer('WorkerCardStatus', {
    'schema': serializers.IntegerField(required=False),
    'method': serializers.ChoiceField(required=False, choices=CARD_METHODS),
    'gpu_mode': serializers.ChoiceField(required=False, choices=CARD_GPU_MODES),
    'env_override': serializers.BooleanField(required=False),
    'revision': serializers.IntegerField(required=False),
    'manager_changes_allowed': serializers.BooleanField(required=False),
    'rebuild_waiting': serializers.BooleanField(required=False),
    'problem': serializers.CharField(required=False),
    'cards': serializers.ListField(required=False, child=_CARD_SCHEMA),
    'alerts': serializers.ListField(required=False, child=_ALERT_SCHEMA),
    'notices': serializers.ListField(required=False, child=_NOTICE_SCHEMA),
    'warnings': serializers.ListField(required=False, child=_WARNING_SCHEMA),
}, required=False, allow_null=True)


class NullWhenEmptyJSONField(serializers.JSONField):
    """A read-only JSON value shown as ``null`` while the stored value is empty (D3)."""

    def __init__(self, **kwargs):
        kwargs.setdefault('read_only', True)
        kwargs.setdefault('allow_null', True)
        super().__init__(**kwargs)

    def to_representation(self, value):
        return super().to_representation(value) if value else None


@extend_schema_field(_CARD_STATUS_SCHEMA)
class CardStatusField(NullWhenEmptyJSONField):
    """Self-reported card report, not verified; ``null`` when empty (D3, R3)."""


def card_alert_dismissals_field():
    """The read-only list of dismissed alert ids (written by the dismiss action)."""
    return serializers.ListField(
        child=serializers.CharField(), read_only=True,
        help_text="Ids of card alerts dismissed by an admin, until the worker stops reporting them.",
    )


# A card change from the manager (FR-CM-4, FR-CM-7, FR-CM-14).  The outcome
# codes are the worker's FR-CM-5 set, with check 6b ``mixed_brands`` (Unreal
# render per card FR-PC-BR-5); ``applied`` exactly when ``result`` is.
CARD_OUTCOME_RESULTS = ('applied', 'refused')
CARD_OUTCOME_CODES = (
    'applied', 'invalid', 'env_override', 'not_allowed', 'probe_failed',
    'identity_mismatch', 'mixed_brands', 'stale_revision', 'card_held', 'save_failed',
)


def _choice_card(name, required):
    ids = {'required': required, 'allow_null': True, 'min_value': 0, 'max_value': 0xFFFF}
    return inline_serializer(name, {
        'key': serializers.CharField(required=required, help_text="Card key: pci:dddd:bb:dd.f or apple-gpu."),
        'vendor_id': serializers.IntegerField(**ids),
        'device_id': serializers.IntegerField(**ids),
    })


def _choice_fields(required, card):
    return {
        'method': serializers.ChoiceField(required=required, choices=CARD_METHODS),
        'gpu_mode': serializers.ChoiceField(required=required, choices=CARD_GPU_MODES),
        'cards': serializers.ListField(required=required, child=card, max_length=16),
        'shown': serializers.ListField(required=required, child=serializers.CharField(), max_length=16),
        'base_revision': serializers.IntegerField(required=required, min_value=0, max_value=2 ** 31 - 1),
    }


_CARD_CHOICE_REQUEST_SCHEMA = inline_serializer('WorkerCardChoiceRequest', {
    **_choice_fields(False, _choice_card('WorkerCardChoiceCard', False)),
    'id': serializers.CharField(required=False),
    'requested_by': serializers.CharField(required=False, help_text='"user id=<pk>", never a username.'),
    'requested_at': serializers.DateTimeField(required=False),
}, required=False, allow_null=True)
_CARD_CHOICE_OUTCOME_SCHEMA = inline_serializer('WorkerCardChoiceOutcome', {
    'id': serializers.CharField(required=False),
    'result': serializers.ChoiceField(required=False, choices=CARD_OUTCOME_RESULTS),
    'code': serializers.ChoiceField(required=False, choices=CARD_OUTCOME_CODES),
    'reason': serializers.CharField(required=False),
    'revision': serializers.IntegerField(required=False),
    'finished_at': serializers.DateTimeField(required=False),
    'requested_by': serializers.CharField(required=False),
    'requested_at': serializers.DateTimeField(required=False),
}, required=False, allow_null=True)


@extend_schema_field(_CARD_CHOICE_REQUEST_SCHEMA)
class CardChoiceRequestField(NullWhenEmptyJSONField):
    """The pending card change sent by an admin; ``null`` when none."""


@extend_schema_field(_CARD_CHOICE_OUTCOME_SCHEMA)
class CardChoiceOutcomeField(NullWhenEmptyJSONField):
    """The worker's last accepted outcome of a card change, not verified; ``null`` when none."""


CardChoiceBody = inline_serializer('CardChoiceBody', _choice_fields(True, _choice_card('CardChoiceBodyCard', True)))
# The 202 answer always carries the new request, so this variant is not nullable.
_SENT_REQUEST = inline_serializer('CardChoiceSentRequest', {
    **_choice_fields(True, _choice_card('CardChoiceSentCard', True)),
    'id': serializers.CharField(),
    'requested_by': serializers.CharField(help_text='"user id=<pk>", never a username.'),
    'requested_at': serializers.DateTimeField(),
})
CardChoiceSendResponse = inline_serializer('CardChoiceSendResponse', {
    'card_choice_request': _SENT_REQUEST,
    'replaced': serializers.CharField(allow_null=True, help_text="Id of the pending change this one replaced."),
})

DismissCardAlertRequest = inline_serializer('DismissCardAlertRequest', {
    'alert_id': serializers.CharField(help_text="Id of an alert the worker reported (32 hex)."),
})
DismissCardAlertResponse = inline_serializer('DismissCardAlertResponse', {
    'card_status': CardStatusField(),
    'card_alert_dismissals': card_alert_dismissals_field(),
})
DetailResponse = inline_serializer('CardActionDetail', {
    'detail': serializers.CharField(),
})
