# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Manager Caddyfile template.

Pure-function renderer for the manager Caddyfile. Given manager-
specific parameters, returns a string containing a valid Caddyfile
that mirrors the manager spec's TLS hardening invariants (manager
spec, "Caddy TLS hardening invariants"):

* ``admin off`` (Caddy admin API disabled)
* ``auto_https off`` (no port 80 bind, no ACME)
* Per-site ``protocols tls1.2 tls1.3`` (TLS floor pinned to 1.2)
* Explicit cert/key paths — no automatic issuance
* ``reverse_proxy`` transport pinned to HTTP/1.1
* ``/api/status/public/`` routes return 404 on the **public** vhost
  (defense-in-depth against URLconf-origin middleware); the loopback
  vhost passes them through
* Access log disabled by default; if enabled, ``Authorization``,
  ``X-Setup-Token``, ``Cookie`` headers AND query-string keys
  ``token``, ``setup_token``, ``password``, ``api_key``, ``secret``
  are redacted (spec Phase 3 line 578)

All input values substituted into the template are validated; any
invalid value raises :class:`ValueError` with a clear message.

Phase 5 status: both Caddy vhosts now reverse-proxy to Waitress
listeners running in the same Django process — the public vhost to
the public-origin Waitress listener, and the loopback vhost to the
internal-origin Waitress listener. The caller passes the two upstream
ports via ``waitress_public_port`` and ``waitress_internal_port``.
Phase 7 removed the legacy alias kwargs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

# ---------------------------------------------------------------------
# Validation primitives (mirror the worker Caddyfile template).
# ---------------------------------------------------------------------

_PORT_MIN = 1024
_PORT_MAX = 65535

# Characters that would break out of a Caddyfile directive or inject
# a directive/comment. Reject before substitution. ``\x00`` is
# included as defense-in-depth: some C-extension stringifiers
# (older libc, certain path APIs) truncate at embedded nulls.
_FORBIDDEN_STRING_CHARS = ('\r', '\n', '{', '}', '#', '`', '\x00')

_TRAVERSAL_TOKENS = ('..',)


def _validate_port(name: str, value: object) -> int:
    """Validate ``value`` is an int in ``[_PORT_MIN, _PORT_MAX]``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"{name} must be an integer port number, got "
            f"{type(value).__name__}: {value!r}"
        )
    if value < _PORT_MIN or value > _PORT_MAX:
        raise ValueError(
            f"{name} must be in [{_PORT_MIN}, {_PORT_MAX}], got {value}"
        )
    return value


def _validate_path_under(
    name: str,
    value: Union[str, Path],
    data_dir: Path,
) -> Path:
    """Validate ``value`` resolves to a path under ``data_dir``."""
    if not isinstance(value, (str, Path)):
        raise ValueError(
            f"{name} must be a filesystem path, got "
            f"{type(value).__name__}: {value!r}"
        )
    raw = str(value)
    if not raw:
        raise ValueError(f"{name} must not be empty")
    if '\x00' in raw:
        raise ValueError(
            f"{name} must not contain null bytes: {raw!r}"
        )
    for token in _TRAVERSAL_TOKENS:
        if token in raw.split('/') or token in raw.split('\\'):
            raise ValueError(
                f"{name} must not contain path-traversal tokens: "
                f"{raw!r}"
            )
    resolved = Path(raw).resolve()
    data_dir_resolved = Path(data_dir).resolve()
    try:
        resolved.relative_to(data_dir_resolved)
    except ValueError as exc:
        raise ValueError(
            f"{name} must resolve inside the manager data dir "
            f"({data_dir_resolved}); got {resolved}"
        ) from exc
    return resolved


def _validate_plain_string(name: str, value: object) -> str:
    """Validate ``value`` is a string free of Caddyfile meta-chars."""
    if not isinstance(value, str):
        raise ValueError(
            f"{name} must be a string, got "
            f"{type(value).__name__}: {value!r}"
        )
    for ch in _FORBIDDEN_STRING_CHARS:
        if ch in value:
            raise ValueError(
                f"{name} must not contain the character {ch!r}: "
                f"{value!r}"
            )
    return value


# ---------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------

def render_manager_caddyfile(
    *,
    public_tls_port: int,
    loopback_plaintext_port: int,
    cert_path: Union[str, Path],
    key_path: Union[str, Path],
    manager_data_dir: Union[str, Path],
    waitress_public_port: int,
    waitress_internal_port: int,
) -> str:
    """Render a manager Caddyfile as a string.

    All parameters are keyword-only to make call sites self-
    documenting and to prevent accidental argument-order mistakes.

    :param public_tls_port: Public-facing HTTPS port Caddy binds.
    :param loopback_plaintext_port: Loopback HTTP port Caddy binds
        (tray helper reads ``/api/status/public/`` from here).
    :param waitress_public_port: Plaintext loopback port where the
        Phase 5 public-origin Waitress listener serves the full
        manager URLconf.
    :param waitress_internal_port: Plaintext loopback port where the
        internal-origin Waitress listener serves ``urls_loopback``
        (``/api/status/public/``).
    :param cert_path: TLS certificate file path. MUST resolve inside
        ``manager_data_dir``.
    :param key_path: TLS private key file path. MUST resolve inside
        ``manager_data_dir``.
    :param manager_data_dir: Manager per-user data directory
        (``%LOCALAPPDATA%\\Sethlans\\manager\\`` on Windows, etc.).
        Used as the containment root for cert/key path validation.
    :returns: Caddyfile content as a string.
    :raises ValueError: Any parameter fails validation.
    """
    data_dir = Path(manager_data_dir).resolve()

    pub_port = _validate_port('public_tls_port', public_tls_port)
    loop_port = _validate_port(
        'loopback_plaintext_port', loopback_plaintext_port,
    )
    uvi_port = _validate_port(
        'waitress_public_port', waitress_public_port,
    )
    wloop_port = _validate_port(
        'waitress_internal_port', waitress_internal_port,
    )
    cert = _validate_path_under('cert_path', cert_path, data_dir)
    key = _validate_path_under('key_path', key_path, data_dir)

    cert_str = _validate_plain_string('cert_path', str(cert))
    key_str = _validate_plain_string('key_path', str(key))

    if len({pub_port, loop_port, uvi_port, wloop_port}) != 4:
        raise ValueError(
            "public_tls_port, loopback_plaintext_port, "
            "waitress_public_port, and waitress_internal_port must "
            f"all be distinct; got public={pub_port} loopback={loop_port} "
            f"waitress_public={uvi_port} waitress_internal={wloop_port}"
        )

    return (
        "# Sethlans Reborn manager Caddyfile (auto-generated).\n"
        "# Do not edit by hand; regenerate via the manager launcher.\n"
        "\n"
        "{\n"
        "    admin off\n"
        "    auto_https off\n"
        "    servers {\n"
        "        protocols h1\n"
        "    }\n"
        "}\n"
        "\n"
        f"# Public TLS vhost (LAN-reachable). Listens on :{pub_port}.\n"
        "# /api/status/public/ returns 404 here — it's a loopback-\n"
        "# only tray helper endpoint. The URLconf-origin Django\n"
        "# middleware is the second line of defence.\n"
        f":{pub_port} {{\n"
        "    tls " + cert_str + " " + key_str + " {\n"
        "        protocols tls1.2 tls1.3\n"
        "    }\n"
        "\n"
        "    @loopback_only {\n"
        "        path /api/status/public /api/status/public/*\n"
        "    }\n"
        "    respond @loopback_only 404\n"
        "\n"
        "    reverse_proxy 127.0.0.1:" + str(uvi_port) + " {\n"
        "        transport http {\n"
        "            versions 1.1\n"
        "        }\n"
        "        # Explicit — Django's SECURE_PROXY_SSL_HEADER reads\n"
        "        # these to know the original request was HTTPS so it\n"
        "        # builds correct absolute URLs for media/assets.\n"
        "        header_up X-Forwarded-Proto https\n"
        "        header_up X-Forwarded-Host {host}\n"
        "    }\n"
        "}\n"
        "\n"
        f"# Loopback plaintext vhost. Listens on 127.0.0.1:{loop_port}.\n"
        "# Serves the tray helper's /api/status/public/ endpoint.\n"
        "# Proxies to the internal-origin Waitress listener (Phase 5).\n"
        f"http://127.0.0.1:{loop_port} {{\n"
        "    reverse_proxy 127.0.0.1:"
        + str(wloop_port) + " {\n"
        "        transport http {\n"
        "            versions 1.1\n"
        "        }\n"
        "        header_up X-Forwarded-Proto http\n"
        "    }\n"
        "}\n"
    )
