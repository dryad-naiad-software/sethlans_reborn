# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``POST /api/wizard/done/`` — final-step handler (FR-W9 / FR-W9a).

Spec 1 / A4. Writes the HMAC-signed ``.wizard_done`` IPC marker
(FR-IPC1) and clears the in-memory session token, so the launcher's
poller can read the marker and proceed to spawn the runtime per
``topology.json``.

Behaviour:

* Requires a valid ``X-Wizard-Session`` header (FR-W9 + FR-W12). Wrong
  / missing header → HTTP 401.
* Refuses to fire unless ``<data_dir>/topology.json`` exists (FR-W8 +
  FR-W9 ordering invariant). Missing topology → HTTP 400.
* Idempotent under the singleton handoff-state lock per FR-W9a /
  CONC-v23-MED-1: the lock is held ONLY around the
  ``check_and_set_done_sent()`` flag transition, NOT around the marker
  write or the session-token clear (those happen unlocked once the flag
  has been claimed).
* On the first successful call, writes the marker and returns
  ``{"status": "ok"}``. On any subsequent call (after the flag flipped),
  returns ``{"status": "already_done"}`` without re-writing the marker.
* After writing the marker, clears the session token via
  ``auth_state.clear_session_token()`` (FR-W7 — "session token cleared
  from memory before exit delay").

Lock ordering (CRITICAL — CONC-v23-MED-1): this handler only ever
acquires the singleton handoff-state lock (via
``auth_state.check_and_set_done_sent``). It MUST NOT acquire the
in-flight probe lock owned by ``handlers/runtime_ready.py``. The
runtime-ready handler may acquire the handoff-state lock first, then
its in-flight probe lock — never the reverse — and that ordering is
unaffected by this handler.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Iterable

from wizard.sethlans_wizard import auth_state, ipc, shutdown
from wizard.sethlans_wizard.handlers import _wsgi
from wizard.sethlans_wizard.handlers.auth import session_header_valid
from wizard.sethlans_wizard.handlers.topology import TOPOLOGY_FILENAME

logger = logging.getLogger(__name__)


def make_done_handler(
    data_dir: Path,
    ipc_secret: bytes,
    wizard_port: int = 0,
) -> Callable:
    """Return a WSGI handler bound to *data_dir* / *ipc_secret* / port.

    Args:
        data_dir: The wizard's per-user data directory (FR-W16
            canonicalised by the wizard's startup code).
        ipc_secret: HMAC secret bytes for the marker IPC. Held in the
            handler's closure; never logged.
        wizard_port: The port the wizard's HTTPS server is bound on.
            Embedded in the marker payload per FR-IPC1. Defaults to 0
            so the handler can be constructed before the listener is
            bound (A6 will pass the resolved port).
    """
    if not isinstance(data_dir, Path):
        data_dir = Path(data_dir)
    if not isinstance(ipc_secret, (bytes, bytearray)) or not ipc_secret:
        raise ValueError("ipc_secret must be non-empty bytes")
    secret = bytes(ipc_secret)

    def handler(environ: dict, start_response: Callable) -> Iterable[bytes]:
        return _handle(environ, start_response, data_dir, secret, wizard_port)

    return handler


def _handle(
    environ: dict,
    start_response: Callable,
    data_dir: Path,
    ipc_secret: bytes,
    wizard_port: int,
) -> Iterable[bytes]:
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method != "POST":
        return _wsgi.send_json(
            start_response,
            {"error": "Method Not Allowed"},
            status=405,
            extra_headers=[("Allow", "POST")],
        )

    if _wsgi.query_string_has_forbidden_key(environ):
        logger.warning(
            "Refused done request with token-shaped query string from %s",
            _wsgi.client_ip(environ),
        )
        return _wsgi.send_json(
            start_response,
            {"error": "session token must not appear in URL"},
            status=400,
        )

    if not session_header_valid(environ):
        return _wsgi.send_json(
            start_response,
            {"error": "missing or invalid X-Wizard-Session header"},
            status=401,
        )

    # FR-W8 / FR-W9 ordering: topology must be persisted before done.
    topology_path = data_dir / TOPOLOGY_FILENAME
    topology = _read_topology(topology_path)
    if topology is None:
        return _wsgi.send_json(
            start_response,
            {"error": "topology not yet selected; POST /api/wizard/topology/ first"},
            status=400,
        )

    # FR-W9a — idempotent under handoff-state lock. The lock is held
    # ONLY around the flag transition (CONC-v23-MED-1).
    is_first = auth_state.check_and_set_done_sent()
    if not is_first:
        logger.info("Done called again; returning already_done")
        return _wsgi.send_json(
            start_response,
            {"status": "already_done"},
            status=200,
        )

    # First-call path: write the marker (no lock held) then clear the
    # session token from memory per FR-W7's "before exit delay" rule.
    marker_path = data_dir / "wizard" / ipc.MARKER_WIZARD_DONE
    try:
        ipc.write_marker(
            marker_path,
            "wizard_done",
            data_dir,
            ipc_secret,
            payload={
                "topology": topology,
                "wizard_port": int(wizard_port),
                "wizard_pid": os.getpid(),
            },
        )
    except OSError as exc:
        logger.error("Could not write .wizard_done marker: %s", exc)
        # FAIL-FATAL: the FR-W9a `_done_sent` flag was already flipped by
        # `check_and_set_done_sent()` above, BEFORE this write attempt.
        # That flip is intentional and not reversible here — it preserves
        # the FR-W9a "exactly one marker" invariant under concurrent
        # callers. The unfortunate side-effect is that any retry POST to
        # /done/ now hits the idempotent branch and returns
        # "already_done" without re-attempting the write.
        #
        # No recovery path exists from the wizard side once this branch
        # is reached. The operator's only option is to abort the wizard
        # via the launcher's `.wizard_reject` flow (FR-W17(b)); the
        # session token is intentionally NOT cleared here so that
        # in-flight requests still authenticate while the operator
        # decides to abort. The launcher will surface the failure via
        # the FR-W17 polite-shutdown machinery.
        return _wsgi.send_json(
            start_response,
            {"error": "could not write .wizard_done marker"},
            status=500,
        )

    auth_state.clear_session_token()
    # FR-W17 — signal the post-done polling thread that .wizard_done
    # is now on disk, arming conditions (b) and (c). Idempotent.
    shutdown.mark_done_written()
    logger.info("Wizard done; .wizard_done written, session cleared")
    return _wsgi.send_json(start_response, {"status": "ok"}, status=200)


def _read_topology(path: Path) -> str | None:
    """Return the persisted topology string, or None if unreadable."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Could not read topology %s: %s", path, exc)
        return None
    try:
        import json

        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    topology = payload.get("topology")
    if not isinstance(topology, str):
        return None
    return topology


__all__ = ["make_done_handler"]
