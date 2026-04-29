# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``POST /api/wizard/verify/`` — Verify checklist (FR-M2-8 / FR-M2-8a).

Topology-aware checklist:

* ``network_bindable`` — re-trial ``socket.bind()`` on the configured
  ``(bind_host, bind_port)``.
* ``database_reachable`` — re-open the configured DB and run
  ``SELECT 1``.
* ``ffmpeg_runs`` — re-execute ``ffmpeg -version``; pass if the
  expected version pin appears.
* ``pending_setup_writable`` — write a temp file under the manager
  data dir and ``os.replace`` it.
* ``worker_password_hashed`` — manager_worker only — assert the
  in-memory wizard state has a non-empty hash.

Concurrency: a per-handler ``threading.Lock`` (the "verify-in-progress
lock") serializes concurrent invocations (FR-M2-8a). A 60-second
result cache lets a double-click see the same answer instead of
re-running every check.
"""

from __future__ import annotations

import configparser
import logging
import socket
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from wizard.sethlans_wizard import progress, wizard_state, ffmpeg_download
from wizard.sethlans_wizard.checkpoints import VERIFIED
from wizard.sethlans_wizard.handlers import _wsgi
from wizard.sethlans_wizard.handlers.auth import session_header_valid
from wizard.sethlans_wizard.handlers import database as database_handler

logger = logging.getLogger(__name__)

VERIFY_SOCKET_TIMEOUT = 10
VERIFY_SUBPROCESS_TIMEOUT = 5
VERIFY_RESULT_CACHE_SECONDS = 60

# FR-M2-8a — per-handler lock + cache.
_verify_lock: threading.Lock = threading.Lock()
_cached_at: float = 0.0
_cached_result: Optional[dict] = None


def _read_manager_ini(data_dir: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    target = data_dir / "manager.ini"
    if target.exists():
        try:
            parser.read(str(target), encoding="utf-8")
        except configparser.Error as exc:
            logger.warning("Could not parse manager.ini: %s", exc)
    return parser


def _check_network_bindable(parser: configparser.ConfigParser) -> dict:
    if not parser.has_section("server"):
        return {"name": "network_bindable", "passed": False,
                "error": "manager.ini [server] missing"}
    host = parser.get("server", "bind_host", fallback="127.0.0.1")
    port_str = parser.get("server", "bind_port", fallback="8080")
    try:
        port = int(port_str)
    except ValueError:
        return {"name": "network_bindable", "passed": False,
                "error": "bind_port not an int"}
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(VERIFY_SOCKET_TIMEOUT)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError as exc:
        logger.info("verify network_bindable failed: %s", exc)
        return {"name": "network_bindable", "passed": False,
                "error": "bind failed"}
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return {"name": "network_bindable", "passed": True, "error": None}


def _check_database_reachable(
    parser: configparser.ConfigParser,
    data_dir: Path,
) -> dict:
    if not parser.has_section("database"):
        return {"name": "database_reachable", "passed": False,
                "error": "manager.ini [database] missing"}
    engine = parser.get("database", "engine", fallback="sqlite")
    payload = {
        "engine": engine,
        "name": parser.get("database", "name", fallback=""),
        "host": parser.get("database", "host", fallback=""),
        "port": parser.getint("database", "port", fallback=0),
        "user": parser.get("database", "user", fallback=""),
        "password": parser.get("database", "password", fallback=""),
        "engine_path": parser.get("database", "engine_path", fallback=""),
    }
    ok, category = database_handler._live_connect(engine, payload, data_dir)
    return {
        "name": "database_reachable",
        "passed": bool(ok),
        "error": None if ok else (category or "generic"),
    }


def _check_ffmpeg_runs(data_dir: Path) -> dict:
    binary = ffmpeg_download.get_ffmpeg_binary(data_dir)
    if binary is None:
        return {"name": "ffmpeg_runs", "passed": False,
                "error": "binary not found"}
    ok, version_str = ffmpeg_download.run_version_check(
        binary, timeout=VERIFY_SUBPROCESS_TIMEOUT,
    )
    if not ok:
        return {"name": "ffmpeg_runs", "passed": False,
                "error": "ffmpeg -version failed"}
    if ffmpeg_download.FFMPEG_VERSION not in version_str:
        return {"name": "ffmpeg_runs", "passed": False,
                "error": "version mismatch"}
    return {"name": "ffmpeg_runs", "passed": True, "error": None}


def _check_pending_setup_writable(data_dir: Path) -> dict:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".sethlans_pending_probe"
        tmp = data_dir / ".sethlans_pending_probe.tmp"
        tmp.write_text("probe", encoding="utf-8")
        import os
        os.replace(str(tmp), str(probe))
        probe.unlink()
    except OSError as exc:
        logger.info("verify pending_setup_writable failed: %s", exc)
        return {"name": "pending_setup_writable", "passed": False,
                "error": "data_dir not writable"}
    return {"name": "pending_setup_writable", "passed": True, "error": None}


def _check_worker_password_hashed() -> dict:
    state = wizard_state.get_worker_password()
    if state is None:
        return {"name": "worker_password_hashed", "passed": False,
                "error": "worker password not yet set"}
    return {"name": "worker_password_hashed", "passed": True, "error": None}


def _read_topology(data_dir: Path) -> Optional[str]:
    target = data_dir / "topology.json"
    if not target.exists():
        return None
    import json
    try:
        payload = json.loads(target.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    topo = payload.get("topology")
    return topo if isinstance(topo, str) else None


def _run_checks(data_dir: Path) -> dict:
    parser = _read_manager_ini(data_dir)
    topology = _read_topology(data_dir) or "manager"
    checks = [
        _check_network_bindable(parser),
        _check_database_reachable(parser, data_dir),
        _check_ffmpeg_runs(data_dir),
        _check_pending_setup_writable(data_dir),
    ]
    if topology == "manager_worker":
        checks.append(_check_worker_password_hashed())
    all_passed = all(c["passed"] for c in checks)
    return {"checks": checks, "all_passed": all_passed}


def make_verify_handler(data_dir: Path) -> Callable:
    """Return a WSGI handler bound to *data_dir* for FR-M2-8."""
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
    global _cached_at, _cached_result
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method != "POST":
        return _wsgi.send_json(
            start_response,
            {"error": "Method Not Allowed"},
            status=405,
            extra_headers=[("Allow", "POST")],
        )
    if not session_header_valid(environ):
        return _wsgi.send_json(
            start_response,
            {"error": "missing or invalid X-Wizard-Session header"},
            status=401,
        )
    # FR-M2-8a — serialize + cache.
    with _verify_lock:
        now = time.monotonic()
        if (
            _cached_result is not None
            and (now - _cached_at) < VERIFY_RESULT_CACHE_SECONDS
        ):
            result = _cached_result
        else:
            result = _run_checks(data_dir)
            _cached_at = now
            _cached_result = result
    if result["all_passed"]:
        progress.append_checkpoint(data_dir, VERIFIED)
    return _wsgi.send_json(start_response, result, status=200)


def reset_cache_for_tests() -> None:
    """Drop the verify cache. Test-only helper."""
    global _cached_at, _cached_result
    with _verify_lock:
        _cached_at = 0.0
        _cached_result = None


__all__ = ["make_verify_handler", "reset_cache_for_tests"]
