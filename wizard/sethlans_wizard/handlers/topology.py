# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``POST /api/wizard/topology/`` — topology selection handler (FR-W8).

Spec 1 / A4. Persists the operator's topology choice to
``<data_dir>/topology.json`` (FR-CFG1) so the launcher can spawn the
right runtime component(s) on the next launch.

Behaviour:

* Accepts ``{"topology": "manager" | "manager_worker" | "worker_only"}``
  as a JSON request body. Other values reject with HTTP 400.
* Requires a valid ``X-Wizard-Session`` header (FR-W8 + FR-W12) — calls
  into ``handlers/auth.session_header_valid``. Missing / wrong header
  yields HTTP 401.
* Writes ``<data_dir>/topology.json`` atomically (temp + ``os.replace``)
  with mode ``0o600`` on POSIX / ACL-restricted on Windows. Schema is
  the v1 envelope from FR-CFG1: ``schema_version``, ``topology``,
  ``chosen_at`` (ISO-8601 UTC).
* Returns ``{"status": "ok"}`` on success.

Stale-cleanup (FR-W8 second sentence):

* The handler exposes :func:`cleanup_stale_topology` for the wizard
  startup path to call. The startup code is wired in A6 — for A4 the
  helper exists as a public symbol so the launcher / startup glue can
  invoke it before serving any requests.
* Within the request handler itself, stale-cleanup is NOT performed:
  by the time the handler runs we are explicitly REPLACING
  ``topology.json`` anyway (the atomic write is idempotent).
"""

from __future__ import annotations

import json
import logging
import os
import platform
import time
from pathlib import Path
from typing import Callable, Iterable

from shared.file_acls import tighten_acls_windows
from wizard.sethlans_wizard import progress
from wizard.sethlans_wizard.checkpoints import TOPOLOGY_CHOSEN
from wizard.sethlans_wizard.handlers import _wsgi
from wizard.sethlans_wizard.handlers.auth import session_header_valid

logger = logging.getLogger(__name__)

VALID_TOPOLOGIES = frozenset({"manager", "manager_worker", "worker_only"})
TOPOLOGY_SCHEMA_VERSION = 1
TOPOLOGY_FILENAME = "topology.json"


# ---------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------

def cleanup_stale_topology(data_dir: Path, sentinel_path: Path) -> bool:
    """Delete a stale ``topology.json`` if no setup-complete sentinel exists.

    FR-W8 second sentence. The wizard may have crashed mid-setup, leaving
    a stale ``topology.json`` from a prior run. If no
    ``.setup_complete`` sentinel is present, the topology file is
    meaningful only as wreckage from the prior crashed run and MUST be
    removed before the user can interact (otherwise re-spawning the
    wizard would still let `done` proceed against the stale topology).

    Returns True if a stale file was removed, False otherwise.
    """
    topology_path = Path(data_dir) / TOPOLOGY_FILENAME
    if not topology_path.exists():
        return False
    if Path(sentinel_path).exists():
        # Sentinel exists — topology.json is the legitimate prior choice.
        return False
    try:
        topology_path.unlink()
        logger.warning(
            "Removed stale topology.json (no .setup_complete sentinel): %s",
            topology_path,
        )
        return True
    except OSError as exc:
        logger.error("Could not remove stale topology %s: %s", topology_path, exc)
        return False


def write_topology_atomic(data_dir: Path, topology: str) -> Path:
    """Write ``<data_dir>/topology.json`` atomically (FR-CFG1)."""
    if topology not in VALID_TOPOLOGIES:
        raise ValueError(f"invalid topology: {topology!r}")
    target = Path(data_dir) / TOPOLOGY_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": TOPOLOGY_SCHEMA_VERSION,
        "topology": topology,
        # FR-CFG1: ISO-8601 UTC timestamp.
        "chosen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    body = json.dumps(payload).encode("utf-8")
    tmp = target.with_suffix(target.suffix + ".tmp")
    fd = os.open(
        str(tmp),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        os.write(fd, body)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(target))
    if platform.system() == "Windows":
        tighten_acls_windows(target)
    else:
        try:
            os.chmod(str(target), 0o600)
        except OSError as exc:
            logger.warning("Could not chmod topology.json %s: %s", target, exc)
    return target


# ---------------------------------------------------------------------
# Handler factory + dispatcher
# ---------------------------------------------------------------------

def make_topology_handler(data_dir: Path) -> Callable:
    """Return a WSGI handler bound to *data_dir* for FR-W8."""
    if not isinstance(data_dir, Path):
        data_dir = Path(data_dir)

    def handler(environ: dict, start_response: Callable) -> Iterable[bytes]:
        return _handle(environ, start_response, data_dir)

    return handler


def _handle(
    environ: dict,
    start_response: Callable,
    data_dir: Path,
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
            "Refused topology request with token-shaped query string from %s",
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

    body = _wsgi.read_body(environ)
    if len(body) > _wsgi.BODY_MAX:
        return _wsgi.send_json(
            start_response,
            {"error": "request body too large"},
            status=400,
        )

    payload = _wsgi.parse_json_body(body)
    if payload is None:
        return _wsgi.send_json(
            start_response,
            {"error": "request body must be JSON {\"topology\": \"...\"}"},
            status=400,
        )
    topology = payload.get("topology")
    if not isinstance(topology, str) or topology not in VALID_TOPOLOGIES:
        return _wsgi.send_json(
            start_response,
            {
                "error": "topology must be one of "
                "manager | manager_worker | worker_only",
            },
            status=400,
        )

    try:
        write_topology_atomic(data_dir, topology)
    except OSError as exc:
        logger.error(
            "Could not write topology.json under %s: %s", data_dir, exc,
        )
        return _wsgi.send_json(
            start_response,
            {"error": "could not write topology.json"},
            status=500,
        )

    _record_topology_checkpoint(data_dir, topology)
    logger.info("Topology selected: %s", topology)
    return _wsgi.send_json(start_response, {"status": "ok"}, status=200)


def _record_topology_checkpoint(data_dir: Path, topology: str) -> None:
    """FR-CHK4 — append topology_chosen to the progress file.

    Best effort: the ``topology.json`` write is the source of truth; the
    progress checkpoint is for resume logic only and is idempotent.
    """
    try:
        progress.append_checkpoint(data_dir, TOPOLOGY_CHOSEN, topology=topology)
    except OSError as exc:
        logger.warning(
            "Could not record topology_chosen checkpoint under %s: %s",
            data_dir, exc,
        )


__all__ = [
    "VALID_TOPOLOGIES",
    "TOPOLOGY_SCHEMA_VERSION",
    "TOPOLOGY_FILENAME",
    "cleanup_stale_topology",
    "write_topology_atomic",
    "make_topology_handler",
]
