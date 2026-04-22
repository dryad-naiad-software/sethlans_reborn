# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared TLS context builder for Sethlans HTTPS endpoints.

Used by both the manager (via run_manager.py / tls_setup.py for
certificate material consumed by Caddy) and the worker (via
web_ui/server.py) to guarantee a consistent TLS floor across all
Sethlans HTTPS endpoints.
"""

import ssl


def build_ssl_context(cert_path, key_path):
    """Build an SSLContext enforcing TLS 1.2 minimum.

    Used by both the manager (via run_manager.py / tls_setup.py) and
    the worker (via web_ui/server.py) to guarantee a consistent TLS
    floor across all Sethlans HTTPS endpoints.
    """
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ssl_ctx.load_cert_chain(str(cert_path), str(key_path))
    return ssl_ctx
