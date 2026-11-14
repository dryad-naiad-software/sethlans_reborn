# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker API token issuance shared by both enrollment paths.

Every successful enrollment (``POST /api/enroll/`` and the in-process
``services.auto_enroll`` path) rotates the worker's token instead of
reusing whatever key the ``worker_<host>`` user already had.  Deleting a
``Worker`` row leaves its user and that user's DRF ``Token`` behind, so
reusing the key would let whoever held the old token keep using it after
the machine is re-enrolled (issue #221).
"""

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework.authtoken.models import Token


def issue_fresh_token(user) -> Token:
    """Delete every DRF token of *user* and return a newly created one.

    Must run inside the caller's ``transaction.atomic()`` block, so the
    rotation commits or rolls back together with the worker linking.
    The user row is locked ``FOR UPDATE`` first (a no-op on SQLite) so
    two concurrent enrollments of the same host serialize: the second
    one waits, then deletes the first one's key and issues its own,
    instead of failing on ``Token.user``'s unique constraint.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("issue_fresh_token() requires an atomic block.")
    user_model = get_user_model()
    list(
        user_model.objects.select_for_update()
        .filter(pk=user.pk).values_list('pk', flat=True),
    )
    Token.objects.filter(user=user).delete()
    return Token.objects.create(user=user)
