# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC

"""
Worker Web UI package.

Provides an embedded HTTP server for a local status dashboard and
authenticated control endpoints. Uses only Python stdlib modules.
"""

from sethlans_worker_agent.web_ui.server import start_server, stop_server

__all__ = ['start_server', 'stop_server']
