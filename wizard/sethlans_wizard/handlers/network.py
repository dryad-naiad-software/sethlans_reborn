# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
r"""``POST /api/wizard/network/`` — Network step (FR-M2-3).

Validates the operator-supplied bind host / port / data-dir, persists
``[server]`` to ``manager.ini`` via the FR-M2-INI helper, and records
the ``network_configured`` checkpoint.

Validation:

* ``bind_host`` must be a valid hostname/IP string. Real ``socket.bind``
  is performed against the chosen ``(bind_host, bind_port)``; the
  socket is closed immediately on success.
* ``bind_port`` must be an integer in [1, 65535].
* ``data_dir`` (optional override) goes through path-traversal hardening
  per FR-M2-3 / security-reviewer MED-4 — reject ``..`` segments,
  require absolute path, ``realpath`` re-check, denylist forbidden
  roots (``/etc``, ``/proc``, ``/sys``, ``/dev``, ``/root``, ``/boot``
  on POSIX; ``\\?\``, ``C:\Windows``, ``C:\Program Files\WindowsApps``
  on Windows).
"""

from __future__ import annotations

import logging
import os
import platform
import socket
from pathlib import Path
from typing import Callable, Iterable, Optional

from wizard.sethlans_wizard import progress
from wizard.sethlans_wizard.checkpoints import NETWORK_CONFIGURED
from wizard.sethlans_wizard.handlers import _wsgi
from wizard.sethlans_wizard.handlers.auth import session_header_valid
from wizard.sethlans_wizard.manager_ini import update_manager_ini

logger = logging.getLogger(__name__)


# ---- Path-traversal hardening (FR-M2-3, security-reviewer MED-4) ----

_POSIX_FORBIDDEN_ROOTS = (
    "/etc", "/proc", "/sys", "/dev", "/root", "/boot",
)
_WINDOWS_FORBIDDEN_ROOTS = (
    "C:\\Windows", "C:\\Program Files\\WindowsApps",
)
_WINDOWS_DEVICE_NAMESPACE_PREFIXES = ("\\\\?\\", "\\\\.\\")


def _resolved_forbidden_roots() -> tuple[str, ...]:
    """Return the platform forbidden-root list, each resolved through realpath.

    macOS aliases ``/etc``, ``/tmp``, ``/var`` to ``/private/etc`` etc. via
    a filesystem-level symlink, so a denylist of literal POSIX paths
    misses inputs that ``Path.resolve()`` returns in their realpath form.
    Resolving the denylist itself once keeps the comparison platform-
    independent without per-OS branching.
    """
    raw = (
        _WINDOWS_FORBIDDEN_ROOTS if platform.system() == "Windows"
        else _POSIX_FORBIDDEN_ROOTS
    )
    resolved: list[str] = []
    for root in raw:
        resolved.append(root)
        try:
            real = os.path.realpath(root)
        except OSError:
            continue
        if real != root:
            resolved.append(real)
    return tuple(resolved)


def _is_under(path: str, root: str) -> bool:
    norm_path = os.path.normcase(os.path.normpath(path))
    norm_root = os.path.normcase(os.path.normpath(root))
    if norm_path == norm_root:
        return True
    sep = os.sep
    return norm_path.startswith(norm_root + sep)


def _check_traversal_or_relative(raw: str) -> Optional[str]:
    """Reject syntactic ``..`` traversal or empty strings."""
    if not isinstance(raw, str) or not raw:
        return "relative"
    if ".." in raw.replace("\\", "/").split("/"):
        return "traversal"
    return None


def _check_windows_device_namespace(raw: str) -> Optional[str]:
    r"""Reject Windows ``\\?\`` and ``\\.\`` device-namespace prefixes."""
    if platform.system() != "Windows":
        return None
    for prefix in _WINDOWS_DEVICE_NAMESPACE_PREFIXES:
        if raw.startswith(prefix):
            return "device_namespace"
    return None


def _check_forbidden_root(canonical_str: str) -> Optional[str]:
    """Return ``"forbidden_root"`` if *canonical_str* is under a denied root.

    The denylist is computed dynamically per call so platform-specific
    realpath aliases (e.g. macOS ``/etc`` -> ``/private/etc``) are
    matched against the resolved input path.
    """
    for root in _resolved_forbidden_roots():
        if _is_under(canonical_str, root):
            return "forbidden_root"
    return None


def validate_data_dir(raw: str) -> tuple[Optional[Path], Optional[str]]:
    """Return ``(canonical_path, None)`` on success or ``(None, code)``.

    Codes (FR-M2-3): ``relative``, ``traversal``, ``forbidden_root``,
    ``device_namespace``.
    """
    code = _check_traversal_or_relative(raw)
    if code is not None:
        return None, code
    code = _check_windows_device_namespace(raw)
    if code is not None:
        return None, code
    candidate = Path(raw)
    if not candidate.is_absolute():
        return None, "relative"
    try:
        canonical = Path(os.path.realpath(str(candidate)))
    except OSError:
        return None, "traversal"
    code = _check_forbidden_root(str(canonical))
    if code is not None:
        return None, code
    return canonical, None


def _try_bind(host: str, port: int) -> Optional[str]:
    """Real ``socket.bind`` against ``(host, port)``.

    Returns ``None`` on success or an error code (``bind_failed``).
    Closes the socket immediately on success.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, int(port)))
    except OSError as exc:
        logger.info("network bind failed for %s:%s: %s", host, port, exc)
        return "bind_failed"
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return None


def make_network_handler(data_dir: Path) -> Callable:
    """Return a WSGI handler bound to *data_dir* for FR-M2-3."""
    if not isinstance(data_dir, Path):
        data_dir = Path(data_dir)

    def handler(environ: dict, start_response: Callable) -> Iterable[bytes]:
        return _handle(environ, start_response, data_dir)

    return handler


def _validate_payload(payload: dict) -> tuple[Optional[dict], Optional[str]]:
    """Return ``(parsed_fields, None)`` on success or ``(None, error_msg)``."""
    bind_host = payload.get("bind_host")
    bind_port = payload.get("bind_port")
    data_dir_override = payload.get("data_dir")
    if not isinstance(bind_host, str) or not bind_host:
        return None, "bind_host must be a non-empty string"
    if not isinstance(bind_port, int) or isinstance(bind_port, bool):
        return None, "bind_port must be an int"
    if not (1 <= bind_port <= 65535):
        return None, "bind_port out of range (1..65535)"
    return {
        "bind_host": bind_host,
        "bind_port": bind_port,
        "data_dir_override": data_dir_override,
    }, None


def _read_request(environ: dict) -> tuple[Optional[dict], Optional[tuple[int, dict]]]:
    """Run the boilerplate request guards. Returns the JSON payload or
    a (status, body) error tuple ready to send back."""
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method != "POST":
        return None, (405, {"error": "Method Not Allowed"})
    if _wsgi.query_string_has_forbidden_key(environ):
        return None, (400, {"error": "session token must not appear in URL"})
    if not session_header_valid(environ):
        return None, (401, {"error": "missing or invalid X-Wizard-Session header"})
    body = _wsgi.read_body(environ)
    if len(body) > _wsgi.BODY_MAX:
        return None, (400, {"error": "request body too large"})
    payload = _wsgi.parse_json_body(body)
    if payload is None:
        return None, (400, {"error": "request body must be JSON"})
    return payload, None


def _handle(
    environ: dict,
    start_response: Callable,
    data_dir: Path,
) -> Iterable[bytes]:
    payload, err = _read_request(environ)
    if err is not None:
        status, body = err
        extra = [("Allow", "POST")] if status == 405 else None
        return _wsgi.send_json(start_response, body, status=status, extra_headers=extra)

    fields, error_msg = _validate_payload(payload)
    if fields is None:
        return _wsgi.send_json(start_response, {"error": error_msg}, status=400)

    target_data_dir = data_dir
    if fields["data_dir_override"] is not None:
        canonical, code = validate_data_dir(fields["data_dir_override"])
        if canonical is None:
            return _wsgi.send_json(
                start_response,
                {"error": "data_dir_invalid", "category": code or "unknown"},
                status=400,
            )
        target_data_dir = canonical

    if _try_bind(fields["bind_host"], fields["bind_port"]) is not None:
        return _wsgi.send_json(
            start_response,
            {"error": "bind_failed",
             "message": "the chosen host/port is not available"},
            status=400,
        )

    try:
        update_manager_ini(
            target_data_dir,
            "server",
            {
                "bind_host": fields["bind_host"],
                "bind_port": fields["bind_port"],
                "data_dir": str(target_data_dir),
            },
        )
    except OSError as exc:
        logger.error("Could not write manager.ini under %s: %s",
                     target_data_dir, exc)
        return _wsgi.send_json(
            start_response,
            {"error": "could not write manager.ini"},
            status=500,
        )

    progress.append_checkpoint(target_data_dir, NETWORK_CONFIGURED)
    return _wsgi.send_json(
        start_response,
        {"status": "ok"},
        status=200,
    )


__all__ = ["make_network_handler", "validate_data_dir"]
