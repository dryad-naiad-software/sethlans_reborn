# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Sending and withdrawing a graphics card change for a worker (worker card
selection FR-CM-10, FR-CM-11, FR-CM-15, D23, D24, S2).

``send`` runs the FR-CM-10 checks in order, the first failing one deciding:
the body length before anything reads the body (411, 413); the worker
(404); the body's shape (400), before any state, so a malformed body is
400 even for a worker that would answer 409; then under the row lock the
worker's reported state (409) and the rules against its stored report
(400).  The rules themselves are in ``card_choice_rules``.  A form body
never gets here: the action parses JSON only (415).  Success stores the
pending request, replacing any earlier one, and appends its id to the
issued list (the last 8), so a late outcome for a replaced or withdrawn
request is still accepted (FR-CM-13).

Every refusal is a :class:`Refused` carrying the HTTP status and one
sentence; 400 and 409 refusals are logged with the user id, never the
body.  ``requested_by`` is ``"user id=<pk>"``, never a username (S2).
"""

import logging
import re
import uuid

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ParseError

from ..models import Worker
from . import card_choice_rules as rules
from .row_locks import lock_own_rows

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 8192
MAX_ISSUED = 8
_DIGITS = re.compile(r'[0-9]{1,12}', re.ASCII)

LENGTH_REQUIRED = "Content-Length is required."
TOO_LARGE = "Request body too large."
NOT_FOUND = "Worker not found."
BAD_JSON = "The body is not valid JSON."
NOTHING_PENDING = "No change is waiting for this worker."


class Refused(Exception):
    """The action is refused with ``status`` and the one-sentence ``detail`` (R6)."""

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _check_length(meta):
    """Step 1, before the body is read: 411 without a length, 413 above 8 KiB or not a number.

    A chunked body reaches Django without ``Content-Length`` only under a
    server that passes it through (the test live server: 411).  Waitress, in
    production, de-chunks it and sets the length, so it meets the 8 KiB cap
    (413) instead.
    """
    raw = meta.get('CONTENT_LENGTH')
    if raw is None or raw == '':
        raise Refused(411, LENGTH_REQUIRED)
    if not isinstance(raw, str) or not _DIGITS.fullmatch(raw) or int(raw) > MAX_BODY_BYTES:
        raise Refused(413, TOO_LARGE)


def _worker_pk(pk):
    """The URL's worker id as an integer; one that cannot be a worker id is a 404, never a database error."""
    if not _DIGITS.fullmatch(str(pk)):
        raise Refused(404, NOT_FOUND)
    return int(pk)


def _body(request):
    """The parsed JSON body; a parse error is 400 (a form body raises 415 from the parser).

    Deep nesting fits in 8 KiB (about 8000 ``[``) and makes the JSON parser
    raise ``RecursionError``: that is a bad body too, never a 500.
    """
    try:
        return request.data
    except (ParseError, RecursionError):
        raise Refused(400, BAD_JSON) from None


def _log_refusal(refused, hostname, user_pk):
    if refused.status in (400, 409):
        logger.info("Graphics card change for worker %s refused (%d): %s (user id=%s)",
                    hostname, refused.status, refused.detail, user_pk)


def _store(pk, body, user_pk):
    """Steps 4-6 under the row lock; return ``(worker, new request, replaced id or None)``."""
    with transaction.atomic():
        worker = lock_own_rows(Worker.objects).filter(pk=pk).first()
        if worker is None:
            raise Refused(404, NOT_FOUND)
        error = rules.state_error(worker.card_status, body['base_revision'])
        if error is not None:
            raise Refused(409, error)
        error = rules.report_error(body, worker.card_status)
        if error is not None:
            raise Refused(400, error)
        pending = worker.card_choice_request
        replaced = pending.get('id') if isinstance(pending, dict) else None
        issued = {'id': uuid.uuid4().hex, 'requested_by': f'user id={user_pk}',
                  'requested_at': timezone.now().isoformat(timespec='seconds')}
        worker.card_choice_request = {**body, **issued}
        history = worker.card_choice_issued if isinstance(worker.card_choice_issued, list) else []
        worker.card_choice_issued = (history + [issued])[-MAX_ISSUED:]
        worker.save(update_fields=['card_choice_request', 'card_choice_issued'])
    return worker, worker.card_choice_request, replaced or None


def send(pk, request):
    """``POST``: check and store a new pending request; return ``(request, replaced id or None)``.

    ``pk`` is the URL's worker id, ``request`` the DRF request (admin-only,
    checked by the action).  Raises :class:`Refused`.
    """
    user_pk = request.user.pk
    _check_length(request.META)
    pk = _worker_pk(pk)
    hostname = Worker.objects.filter(pk=pk).values_list('hostname', flat=True).first()
    if hostname is None:
        raise Refused(404, NOT_FOUND)
    try:
        data = _body(request)
        error = rules.shape_error(data)
        if error is not None:
            raise Refused(400, error)
        worker, pending, replaced = _store(pk, rules.clean_body(data), user_pk)
    except Refused as refused:
        _log_refusal(refused, hostname, user_pk)
        raise
    logger.info("Graphics card change %s for worker %s requested by user id=%s",
                pending['id'], worker.hostname, user_pk)
    return pending, replaced


def withdraw(pk, user_pk):
    """``DELETE``: clear the pending request (its id stays issued, FR-CM-11).  Raises :class:`Refused`."""
    pk = _worker_pk(pk)
    with transaction.atomic():
        worker = lock_own_rows(Worker.objects).filter(pk=pk).first()
        if worker is None:
            raise Refused(404, NOT_FOUND)
        pending = worker.card_choice_request
        if not pending:
            refused = Refused(409, NOTHING_PENDING)
            _log_refusal(refused, worker.hostname, user_pk)
            raise refused
        worker.card_choice_request = {}
        worker.save(update_fields=['card_choice_request'])
    logger.info("Graphics card change %s for worker %s withdrawn by user id=%s",
                pending.get('id') if isinstance(pending, dict) else None, worker.hostname, user_pk)
