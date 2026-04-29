# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Wizard Caddyfile template (issue #170).

Pure-function renderer for the wizard Caddyfile. Given wizard-specific
parameters, returns a string containing a valid Caddyfile that mirrors
the manager / worker TLS hardening invariants:

* ``admin off`` (Caddy admin API disabled)
* ``auto_https off`` (no port 80 bind, no ACME)
* Per-site ``protocols tls1.2 tls1.3`` (TLS floor pinned to 1.2)
* Explicit cert/key paths — no automatic issuance
* ``reverse_proxy`` transport pinned to HTTP/1.1
* No access log directive (matching the manager / worker defaults)

The wizard has only one vhost — public TLS on the launcher's chosen
port — that reverse-proxies to the wizard subprocess's loopback HTTP
listener. Unlike the manager template there is no public/loopback
split, and no setup-route gating: the wizard IS the setup wizard.

NFR-7: validation primitives (``_validate_port``, ``_validate_path_under``,
``_FORBIDDEN_STRING_CHARS``) are duplicated from
``manager/sethlans_manager/caddy_template.py`` and
``worker/sethlans_worker_agent/caddy_template.py`` rather than extracted
to a shared module — extraction is out of scope for #170 (would push the
manager template work past its current line budget). Keep the three
copies in lockstep when you change one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

# ---------------------------------------------------------------------
# Validation primitives (mirror of the manager / worker templates).
# ---------------------------------------------------------------------

_PORT_MIN = 1024
_PORT_MAX = 65535

# Characters that would break out of a Caddyfile directive or inject
# a directive/comment. Reject before substitution. ``\x00`` is
# included as defense-in-depth: some C-extension stringifiers
# (older libc, certain path APIs) truncate at embedded nulls.
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
            f"{name} must resolve inside the wizard data dir "
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

def render_wizard_caddyfile(
    *,
    public_tls_port: int,
    loopback_port: int,
    cert_path: Union[str, Path],
    key_path: Union[str, Path],
    wizard_data_dir: Union[str, Path],
) -> str:
    """Render a wizard Caddyfile as a string.

    All parameters are keyword-only to make call sites self-
    documenting and to prevent accidental argument-order mistakes.

    :param public_tls_port: Public-facing HTTPS port Caddy binds.
        The launcher pins this to 8100 by default.
    :param loopback_port: Plaintext loopback port where the wizard's
        Waitress listener accepts requests. Caddy reverse-proxies to
        ``127.0.0.1:<loopback_port>``.
    :param cert_path: TLS certificate file path. MUST resolve inside
        ``wizard_data_dir``.
    :param key_path: TLS private key file path. MUST resolve inside
        ``wizard_data_dir``.
    :param wizard_data_dir: Wizard per-user data directory
        (``<data_dir>/wizard/`` in practice). Used as the containment
        root for cert/key path validation.
    :returns: Caddyfile content as a string.
    :raises ValueError: Any parameter fails validation.
    """
    data_dir = Path(wizard_data_dir).resolve()

    pub_port = _validate_port('public_tls_port', public_tls_port)
    loop_port = _validate_port('loopback_port', loopback_port)
    cert = _validate_path_under('cert_path', cert_path, data_dir)
    key = _validate_path_under('key_path', key_path, data_dir)

    cert_str = _validate_plain_string('cert_path', str(cert))
    key_str = _validate_plain_string('key_path', str(key))

    if pub_port == loop_port:
        raise ValueError(
            "public_tls_port and loopback_port must differ; got "
            f"public={pub_port} loopback={loop_port}"
        )

    return (
        "# Sethlans Reborn wizard Caddyfile (auto-generated).\n"
        "# Do not edit by hand; regenerate via the launcher.\n"
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
        "# The wizard subprocess listens plain HTTP on the loopback\n"
        "# port below; Caddy terminates TLS for browser-facing\n"
        "# traffic and reverse-proxies through.\n"
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
        "    reverse_proxy http://127.0.0.1:" + str(loop_port) + " {\n"
        "        transport http {\n"
        "            versions 1.1\n"
        "        }\n"
        # Issue #175 — propagate the original scheme to the wizard so
        # the auth handler can decide whether to mark the
        # ``wizard_session`` cookie as Secure. Caddy terminates TLS for
        # the public vhost; the loopback hop is plain HTTP, which would
        # otherwise look like a non-secure context to the wizard.
        "        header_up X-Forwarded-Proto https\n"
        "    }\n"
        "}\n"
    )
