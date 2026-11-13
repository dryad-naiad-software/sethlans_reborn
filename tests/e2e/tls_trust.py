# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
TLS trust helpers for E2E HTTPS clients.

The test manager generates a self-signed certificate at
``<SETHLANS_TLS_DATA_DIR>/cert.pem`` (``shared.cert_utils``) and Caddy
serves that exact certificate on the public TLS port. Its SAN always
covers ``127.0.0.1`` and ``localhost``, so test clients can pin it as
their CA bundle and keep full certificate + hostname verification on
(issue #209) instead of passing ``verify=False``.
"""

from pathlib import Path

import requests


def manager_cert_path(env) -> Path:
    """Return the cert the manager built from ``env`` serves over TLS.

    ``env`` is the dict produced by ``build_manager_env``, which always
    sets ``SETHLANS_TLS_DATA_DIR`` to a per-test directory.
    """
    tls_data_dir = env.get("SETHLANS_TLS_DATA_DIR")
    if not tls_data_dir:
        raise RuntimeError(
            "SETHLANS_TLS_DATA_DIR missing from the manager env; build it "
            "with tests.e2e.env_config.build_manager_env."
        )
    return Path(tls_data_dir) / "cert.pem"


def manager_session(env) -> requests.Session:
    """Return a ``requests.Session`` that trusts the test manager's cert."""
    session = requests.Session()
    session.verify = str(manager_cert_path(env))
    return session
