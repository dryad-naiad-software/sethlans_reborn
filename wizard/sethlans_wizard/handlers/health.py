# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``GET /api/health/`` — FR-W14 envelope for the launcher's cold-boot probe.

Anonymous, loopback-only, returns ``{boot_id, version}`` so the
launcher's ``wait_for_health`` helper can detect that the wizard is up
and serving. ``boot_id`` is generated once at handler-build time and is
stable for the wizard process lifetime — matches manager / worker
``/api/health/`` semantics where the value is bound to the process's
runtime_state.

Issue: https://github.com/dryad-naiad-software/sethlans_reborn/issues/160

Hard rules:

* No auth, no setup token, no IPC secret. The launcher's probe is
  anonymous over loopback HTTPS.
* Stdlib JSON only (NFR-1) — no third-party deps.
* The envelope is the manager + worker FR-W14 intersection
  ``{boot_id, version}``; the wizard does NOT include ``worker_id``
  (worker-only field, out of scope per #160).
"""

from __future__ import annotations

import json
import uuid
from typing import Callable, Iterable

from shared import version as _version


def make_health_handler() -> Callable:
    """Return a WSGI handler for ``GET /api/health/``.

    ``boot_id`` is captured once when the handler is built (server
    startup time), so every request during this wizard's lifetime
    returns the same UUID4. The launcher's probe doesn't care about
    uniqueness; it only checks that the keys are present in the
    response body. Two ``make_health_handler()`` calls produce two
    distinct ``boot_id`` values (UUID4 entropy).
    """
    boot_id = str(uuid.uuid4())
    version = _version.get_version()
    body_bytes = json.dumps(
        {"boot_id": boot_id, "version": version},
    ).encode("utf-8")
    body_len = str(len(body_bytes))

    def _handle(
        environ: dict,
        start_response: Callable,
    ) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        if method != "GET":
            payload = b'{"error": "Method Not Allowed"}'
            start_response(
                "405 Method Not Allowed",
                [
                    ("Content-Type", "application/json"),
                    ("Content-Length", str(len(payload))),
                    ("Allow", "GET"),
                ],
            )
            return [payload]
        start_response(
            "200 OK",
            [
                ("Content-Type", "application/json"),
                ("Content-Length", body_len),
            ],
        )
        return [body_bytes]

    return _handle


__all__ = ["make_health_handler"]
