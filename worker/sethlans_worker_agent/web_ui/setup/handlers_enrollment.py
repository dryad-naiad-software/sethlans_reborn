# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup wizard enrollment handler.

POST /api/setup/worker/enroll/ -- enroll with the selected manager.

Phase 4g of the Waitress migration: the final async handler is
rewritten to sync WSGI.  The previous implementation offloaded the
blocking HTTP call to an event-loop executor because
``enrollment_client.enroll()`` performs a synchronous ``requests``
round-trip.  Under Waitress every request already runs on its own
worker thread, so the executor hop is unnecessary: we call
``_do_enroll`` directly on the request thread.

FR-6 timeout invariant: ``enrollment_client._post_enroll`` passes
``timeout=15`` to ``requests.Session.post`` (see
``enrollment_client.py``), so the outbound HTTP call can never hang
the Waitress worker thread indefinitely.  Tests in
``test_setup_handlers_enrollment.py`` lock this invariant.

Lock policy: ``handle_enroll`` mutates ``config_store`` via
``set_many()`` on success, so it is wrapped in
``setup_mutation_lock`` for fail-fast 409 on concurrent wizard POSTs
(FR-18).  The wizard is single-user and inherently serialized, so
holding the lock across the ~15s-bounded outbound HTTP call is
acceptable here even though ``lock.py`` warns against long holds in
general -- a concurrent duplicate enroll POST is always a bug we
want to reject, not queue.
"""

import logging
from typing import Callable, Iterable, Optional

from sethlans_worker_agent.web_ui.http_helpers_wsgi import (
    send_json_wsgi, parse_json_body_wsgi,
)
from sethlans_worker_agent.web_ui.setup.handlers_status import (
    append_wizard_checkpoint,
)
from sethlans_worker_agent.web_ui.setup.handlers_discovery import (
    get_selected_manager_url,
    get_selected_manager_id,
    get_selected_manager_meta,
)
from sethlans_worker_agent.web_ui.setup.lock import (
    setup_mutation_lock,
)

logger = logging.getLogger(__name__)


def _do_enroll(
    manager_url: str,
    enrollment_key: str,
    manager_id: Optional[str],
    manager_meta: Optional[dict],
) -> dict:
    """Run enrollment synchronously (blocking HTTP call).

    Returns ``{"status": "ok", "manager_name": ...}`` on success or
    raises an ``enrollment_client.EnrollmentError`` subclass.

    The underlying ``enrollment_client.enroll`` call carries an
    explicit ``timeout=15`` inside ``_post_enroll`` (FR-6), so this
    function is bounded even when invoked directly on a Waitress
    worker thread.
    """
    from sethlans_worker_agent import (
        enrollment_client, hardware_detection, config_store,
    )

    result = enrollment_client.enroll(
        manager_url, enrollment_key, hardware_detection.HOSTNAME,
    )

    # Persist credentials atomically -- mirrors wizard._persist_config
    pairs = [
        ("manager.api_token", result["api_token"]),
        ("manager.cert_fingerprint", result["cert_fingerprint"]),
        (
            "manager.manager_id",
            result.get("manager_id") or manager_id or "",
        ),
    ]
    if manager_meta:
        host = manager_meta.get("ip") or manager_meta.get("host")
        if host:
            pairs.append(("manager.host", host))
        port = manager_meta.get("port")
        if port:
            pairs.append(("manager.port", int(port)))

    config_store.set_many(pairs)

    # Include manager name from discovery metadata for UX.
    manager_name = (
        (manager_meta or {}).get("name")
        or "Manager"
    )
    return {
        "status": "ok",
        "manager_name": manager_name,
    }


def handle_enroll(
    environ: dict, start_response: Callable,
) -> Iterable[bytes]:
    """POST /api/setup/worker/enroll/ -- Enroll with manager.

    Mutation: wrapped in :func:`setup_mutation_lock` (409 on
    contention).  Validation and ``_do_enroll`` both run inside the
    lock so concurrent duplicate POSTs fail fast rather than racing
    on ``config_store`` writes.
    """
    from sethlans_worker_agent import enrollment_client

    with setup_mutation_lock() as acquired:
        if not acquired:
            return send_json_wsgi(
                start_response,
                {"error": "Setup mutation in progress; retry after "
                          "current operation completes."},
                409,
            )

        data, err = parse_json_body_wsgi(environ)
        if err is not None:
            return send_json_wsgi(start_response, err[0], err[1])

        if not isinstance(data, dict):
            return send_json_wsgi(
                start_response,
                {"error": "Request body must be a JSON object"},
                400,
            )

        enrollment_key = data.get("enrollment_key", "").strip()
        if not enrollment_key:
            return send_json_wsgi(
                start_response,
                {"error": "'enrollment_key' is required."},
                400,
            )

        manager_url = get_selected_manager_url()
        if not manager_url:
            return send_json_wsgi(
                start_response,
                {"error": "No manager selected. Call "
                          "/api/setup/worker/select-manager/ first."},
                400,
            )

        manager_id = get_selected_manager_id()
        manager_meta = get_selected_manager_meta()

        try:
            result = _do_enroll(
                manager_url, enrollment_key, manager_id, manager_meta,
            )
        except enrollment_client.InvalidKeyError as e:
            return send_json_wsgi(
                start_response, {"error": str(e)}, 403,
            )
        except enrollment_client.RateLimitedError as e:
            return send_json_wsgi(
                start_response, {"error": str(e)}, 429,
            )
        except enrollment_client.EnrollmentError as e:
            return send_json_wsgi(
                start_response, {"error": str(e)}, 502,
            )

        append_wizard_checkpoint("enrolled")
        logger.info("Setup wizard: enrollment successful.")
        return send_json_wsgi(start_response, result)
