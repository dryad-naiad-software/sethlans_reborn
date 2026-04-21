# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Worker Web UI package.

Provides an embedded HTTP server (Waitress) for a local status
dashboard and authenticated control endpoints. TLS is terminated
by a Caddy reverse proxy in front of Waitress.
"""

from sethlans_worker_agent.web_ui.server import start_server, stop_server

__all__ = ['start_server', 'stop_server']
