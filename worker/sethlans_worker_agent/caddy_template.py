# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker Caddyfile template.

Pure-function renderer for the worker Caddyfile. Given worker-specific
parameters, returns a string containing a valid Caddyfile that mirrors
the manager spec's TLS hardening invariants:

- ``protocols tls1.2 tls1.3`` (per-site TLS floor pinned to 1.2)
  and explicit cert/key paths
- ``admin off`` (Caddy admin API disabled)
- No port 80 binding, no automatic HTTPS, no ACME
- No access log directive (if added later, ``Authorization``,
  ``X-Setup-Token``, and ``Cookie`` headers must be redacted)
- ``reverse_proxy`` transport pinned to HTTP/1.1
- ``/setup/*`` routes reachable **only** on the loopback vhost during
  setup; the public vhost returns 404 for setup paths until the
  setup-complete sentinel is written

This module is Phase 1 groundwork: it is imported/invoked nowhere in
the worker runtime path. The launcher ENTRYPOINT change and the
template-to-disk write land in Phase 5. Keeping the function pure
(no disk I/O, no subprocess, no network) makes it trivially
unit-testable.

All input values substituted into the template are validated first;
any invalid value raises :class:`ValueError` with a clear message.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

# ---------------------------------------------------------------------
# Validation primitives
# ---------------------------------------------------------------------

# Port range for unprivileged binds; mirrors the manager Caddyfile
# templating validator (manager spec Phase 3 technical approach).
_PORT_MIN = 1024
_PORT_MAX = 65535

# Characters that would break out of a Caddyfile directive or inject
# a directive/comment. Reject before substitution. ``\x00`` is
# included as defense-in-depth: some C-extension stringifiers
# (older libc, certain path APIs) truncate at embedded nulls, which
# could produce surprising behavior even though ``Path.resolve()``
# preserves them verbatim.
_FORBIDDEN_STRING_CHARS = ('\r', '\n', '{', '}', '#', '`', '"', '\x00')

# Path-traversal tokens. ``Path.resolve()`` canonicalises before the
# containment check, but an explicit up-front reject keeps error
# messages clear when a caller passes something obviously wrong.
_TRAVERSAL_TOKENS = ('..',)


def _validate_port(name: str, value: object) -> int:
    """Validate ``value`` is an int in ``[_PORT_MIN, _PORT_MAX]``.

    Booleans are rejected (``bool`` is a subclass of ``int`` in
    Python, so ``isinstance(True, int)`` is truthy — we exclude it).
    """
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
    """Validate ``value`` resolves to a path under ``data_dir``.

    The worker data dir is supplied explicitly by the caller (the
    launcher) — **not** hardcoded — so the shared path-validation
    helper pattern can serve both manager and worker without drift.
    """
    if not isinstance(value, (str, Path)):
        raise ValueError(
            f"{name} must be a filesystem path, got "
            f"{type(value).__name__}: {value!r}"
        )
    raw = str(value)
    if not raw:
        raise ValueError(f"{name} must not be empty")
    if '\x00' in raw:
        # Reject null bytes up front: some C-extension filesystem
        # APIs truncate paths at an embedded null, creating a
        # confusion window between validation and use. The
        # authoritative containment check below still runs, but an
        # explicit reject here produces a clearer error.
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
            f"{name} must resolve inside the worker data dir "
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

def render_worker_caddyfile(
    *,
    public_tls_port: int,
    loopback_plaintext_port: int,
    waitress_upstream_port: int,
    cert_path: Union[str, Path],
    key_path: Union[str, Path],
    worker_data_dir: Union[str, Path],
) -> str:
    """Render a worker Caddyfile as a string.

    All parameters are keyword-only to make call sites self-documenting
    and to prevent accidental argument-order mistakes.

    :param public_tls_port: public-facing HTTPS port Caddy binds.
    :param loopback_plaintext_port: loopback HTTP port Caddy binds
        (setup wizard routes are reachable only on this vhost during
        the setup window).
    :param waitress_upstream_port: plaintext loopback port where
        Waitress serves the WSGI app. Caddy reverse-proxies both
        vhosts to this upstream.
    :param cert_path: TLS certificate file path. MUST resolve inside
        ``worker_data_dir``.
    :param key_path: TLS private key file path. MUST resolve inside
        ``worker_data_dir``.
    :param worker_data_dir: worker per-user data directory
        (``%LOCALAPPDATA%\\Sethlans\\worker\\`` on Windows, etc.).
        Used as the containment root for cert/key path validation.
    :returns: Caddyfile content as a string.
    :raises ValueError: any parameter fails validation.
    """
    data_dir = Path(worker_data_dir).resolve()

    pub_port = _validate_port('public_tls_port', public_tls_port)
    loop_port = _validate_port(
        'loopback_plaintext_port', loopback_plaintext_port,
    )
    up_port = _validate_port(
        'waitress_upstream_port', waitress_upstream_port,
    )
    cert = _validate_path_under('cert_path', cert_path, data_dir)
    key = _validate_path_under('key_path', key_path, data_dir)

    # Additional defence: reject meta-chars on the string forms that
    # will land in the Caddyfile. Paths have already been resolved;
    # this catches pathological values (e.g. embedded newlines on
    # platforms where the filesystem permits them).
    cert_str = _validate_plain_string('cert_path', str(cert))
    key_str = _validate_plain_string('key_path', str(key))

    if len({pub_port, loop_port, up_port}) != 3:
        raise ValueError(
            "public_tls_port, loopback_plaintext_port, and "
            "waitress_upstream_port must all be distinct; got "
            f"public={pub_port} loopback={loop_port} "
            f"upstream={up_port}"
        )

    # Caddyfile content. Uses snippet-free form (no named matchers
    # with block substitution) to keep rendering deterministic and
    # easy to diff.
    return (
        "# Sethlans Reborn worker Caddyfile (auto-generated).\n"
        "# Do not edit by hand; regenerate via the worker launcher.\n"
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
        "# Setup wizard paths return 404 here during the setup\n"
        "# window; only the loopback vhost serves /setup/* and\n"
        "# /api/setup/* until the setup-complete sentinel is\n"
        "# written.\n"
        f":{pub_port} {{\n"
        # Paths are double-quoted so directories that contain spaces
        # (e.g. macOS's ~/Library/Application Support/Sethlans/...)
        # are parsed as a single Caddyfile token rather than split on
        # whitespace. The _FORBIDDEN_STRING_CHARS validator rejects
        # embedded double-quotes so the enclosing quotes are always
        # balanced.
        '    tls "' + cert_str + '" "' + key_str + '" {\n'
        "        protocols tls1.2 tls1.3\n"
        "    }\n"
        "\n"
        "    @setup_paths {\n"
        "        path /setup /setup/* /api/setup /api/setup/*\n"
        "    }\n"
        "    respond @setup_paths 404\n"
        "\n"
        "    reverse_proxy 127.0.0.1:" + str(up_port) + " {\n"
        "        transport http {\n"
        "            versions 1.1\n"
        "        }\n"
        "    }\n"
        "}\n"
        "\n"
        f"# Loopback plaintext vhost. Listens on "
        f"127.0.0.1:{loop_port}.\n"
        "# This vhost serves the worker setup wizard and is the\n"
        "# only origin from which /setup/* is reachable during the\n"
        "# setup window.\n"
        f"http://127.0.0.1:{loop_port} {{\n"
        "    reverse_proxy 127.0.0.1:" + str(up_port) + " {\n"
        "        transport http {\n"
        "            versions 1.1\n"
        "        }\n"
        "    }\n"
        "}\n"
    )
