# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Validators and sanitizers for the worker heartbeat endpoint.

Extracted from ``heartbeat.py`` to keep that module under the 300-line
cap.  These helpers are pure functions over request payload values —
they have no Django ORM or HTTP coupling and are unit-tested in
``tests/unit/manager/test_heartbeat_helpers.py``.
"""

import logging
import re

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator

from ..constants import WorkerStatus

logger = logging.getLogger(__name__)

WORKER_ACCEPTED_STATUSES = {WorkerStatus.IDLE, WorkerStatus.RENDERING}


def _validate_worker_status(raw_status):
    """Validate status from worker payload. Returns a valid status string."""
    if raw_status in WORKER_ACCEPTED_STATUSES:
        return raw_status
    return WorkerStatus.IDLE


def _sanitize_cpu_name(cpu_name):
    """Sanitize cpu_name input, rejecting strings with HTML/script chars."""
    if not isinstance(cpu_name, str):
        return ''
    if not re.match(r'^[\w\s\-().@,/#+]*$', cpu_name, re.ASCII):
        return ''
    return cpu_name


def _validate_ui_url(raw_url):
    """Validate and return a ui_url, or None if invalid."""
    if not raw_url:
        return None
    try:
        URLValidator(schemes=['http', 'https'])(raw_url)
        return raw_url
    except DjangoValidationError:
        return None


def _validate_ui_cert_fingerprint(raw_value):
    """Validate and return a ui_cert_fingerprint, or '' if invalid.

    Accepts a 64-character lowercase hex string (SHA-256).
    Logs a warning and returns '' for non-conforming values.
    """
    if not raw_value:
        return ''
    if not isinstance(raw_value, str):
        logger.warning(
            "Rejecting non-string ui_cert_fingerprint: %r",
            type(raw_value).__name__,
        )
        return ''
    if not re.fullmatch(r'[0-9a-f]{64}', raw_value):
        logger.warning(
            "Rejecting invalid ui_cert_fingerprint: %r",
            raw_value[:80],
        )
        return ''
    return raw_value


def _extract_gpu_name(available_tools):
    """Extract GPU name(s) from available_tools JSON."""
    if not isinstance(available_tools, dict):
        return ''
    details = available_tools.get('gpu_devices_details', [])
    if not isinstance(details, list):
        return ''
    names = [
        d.get('name', '') for d in details
        if isinstance(d, dict) and d.get('name')
    ]
    result = ', '.join(names)
    return result[:255]
