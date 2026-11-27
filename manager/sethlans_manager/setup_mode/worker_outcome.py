# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
What the setup page gets back from the worker program's commands at submit
(FR-WZ-2, FR-WZ-8, FR-WK-5, FR-SEC-9, OQ-7).

:func:`failure` turns a failed command into the submit's error answer:

* an expected refusal (exit 1) is something the person can fix: 400
  ``{"error": <worker code>, "message", "fields": {<dotted field>: [message]}}``,
  on the field the code points at (``FIELD_BY_CODE``) or on
  ``non_field_errors``;
* exit 2, another exit code, a timeout, a program that would not start or
  an unreadable answer: 500 ``{"error": "worker_failed" | "worker_timeout"
  | "worker_unavailable" | "worker_bad_answer", "message"}``.

:func:`finish` is the ``worker`` part of the 200 answer: whether the
worker enrolled now or kept its enrollment, the manager's URL, id and name,
the certificate fingerprint the worker pinned and the ``status`` the
manager sent (worker approval, decision C). Every value is copied as
printable text, never passed through.
"""
from typing import Optional, Tuple

from sethlans_manager.setup_mode import worker_cli
from sethlans_manager.setup_mode.fields import NON_FIELD
from sethlans_manager.setup_mode.clean import clean_text
from sethlans_manager.setup_mode.worker_form import MAX_HOST

FIELD_BY_CODE = {
    "invalid_key": "manager.enrollment_key",
    "unreachable": "manager.url",
    "certificate_mismatch": "manager.url",
    "manager_not_ready": "manager.url",
    "card_changed": "worker.cards",
    "card_held": "worker.cards",
}
MAX_FINGERPRINT = 128


def failure(result: worker_cli.CliResult) -> Tuple[int, dict]:
    """``(HTTP status, body)`` for a failed worker command (module doc)."""
    if result.broken:
        return 500, {"error": result.error, "message": result.message}
    field = FIELD_BY_CODE.get(result.error, NON_FIELD)
    return 400, {"error": result.error, "message": result.message, "fields": {field: [result.message]}}


def _text(answer: dict, key: str, max_length: int = 64) -> Optional[str]:
    return clean_text(answer.get(key), max_length) or None


def _kept_url(enrollment: dict) -> Optional[str]:
    host, port = enrollment.get("host"), enrollment.get("port")
    if not host or not port:
        return None
    return f"https://[{host}]:{port}" if ":" in host else f"https://{host}:{port}"


def finish(result: Optional[dict], manager_url: Optional[str], kept: Optional[dict]) -> Optional[dict]:
    """The ``worker`` object of the finish answer; ``None`` when no worker was set up.

    ``result`` is ``--setup-apply``'s answer, ``manager_url`` the URL it
    enrolled with, ``kept`` the enrollment the worker kept (``worker`` mode
    with ``keep_enrollment``).
    """
    if result is None:
        return None
    enrolled = result.get("enrolled") is True
    info = {
        "enrolled": enrolled,
        "kept_enrollment": not enrolled and kept is not None,
        "manager_url": None,
        "manager_id": None,
        "manager_name": None,
        "cert_fingerprint": None,
        "status": None,
    }
    if enrolled:
        info.update(manager_url=manager_url, manager_id=_text(result, "manager_id"),
                    manager_name=_text(result, "manager_name"),
                    cert_fingerprint=_text(result, "cert_fingerprint", MAX_FINGERPRINT),
                    status=_text(result, "status"))
    elif kept is not None:
        info.update(manager_url=_kept_url(kept), manager_id=kept.get("manager_id"),
                    manager_name=kept.get("manager_name"))
    if info["manager_url"] is not None:
        info["manager_url"] = clean_text(info["manager_url"], MAX_HOST + 16)
    return info
