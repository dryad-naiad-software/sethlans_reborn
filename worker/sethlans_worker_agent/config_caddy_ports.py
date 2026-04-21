# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Caddy / Waitress port layout resolver (Phase 5b).

Keeps the three-port block out of ``config.py`` proper so that module
stays close to its historical footprint. Returns a flat dict consumed
by ``config.py`` at import time.

Port semantics:

* ``CADDY_PUBLIC_TLS_PORT`` — external HTTPS port clients hit.
  Defaults to ``UI_PORT`` so the URL reported by ``system_monitor``
  to the manager is unchanged.
* ``CADDY_LOOPBACK_PORT`` — plaintext loopback vhost Caddy binds on
  127.0.0.1 (setup-wizard-only origin during the setup window).
  Defaults to ``UI_PORT + 1``.
* ``WAITRESS_UPSTREAM_PORT`` — plaintext loopback port Waitress binds;
  Caddy reverse-proxies both vhosts to this upstream. Defaults to
  ``UI_PORT + 1000`` so it does not collide with Caddy's ports.

Environment overrides follow the standard pattern:
``SETHLANS_WORKER_{CADDY_PUBLIC_TLS_PORT,CADDY_LOOPBACK_PORT,
WAITRESS_UPSTREAM_PORT}``.
"""

from __future__ import annotations


def load_caddy_port_config(ui_port: int, get_config_value, validate_int) -> dict:
    """Resolve the three Caddy/Waitress ports from config precedence.

    Parameters mirror the helpers already wired in ``config.py``:

    * ``ui_port`` — the resolved worker ``UI_PORT`` (used as the base
      for the defaults).
    * ``get_config_value`` — the ``config.get_config_value`` callable
      (env > JSON > INI > default).
    * ``validate_int`` — the ``config._validate_int`` bounds checker.
    """
    public = validate_int(
        'worker.caddy_public_tls_port',
        get_config_value(
            'worker', 'caddy_public_tls_port', ui_port, is_int=True,
        ),
        ui_port, 1024, 65535,
    )
    loopback = validate_int(
        'worker.caddy_loopback_port',
        get_config_value(
            'worker', 'caddy_loopback_port', ui_port + 1, is_int=True,
        ),
        ui_port + 1, 1024, 65535,
    )
    upstream = validate_int(
        'worker.waitress_upstream_port',
        get_config_value(
            'worker', 'waitress_upstream_port', ui_port + 1000, is_int=True,
        ),
        ui_port + 1000, 1024, 65535,
    )
    return {
        'CADDY_PUBLIC_TLS_PORT': public,
        'CADDY_LOOPBACK_PORT': loopback,
        'WAITRESS_UPSTREAM_PORT': upstream,
    }
