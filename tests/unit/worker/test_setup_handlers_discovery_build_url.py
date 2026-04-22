# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the ``_build_manager_url`` helper.

Extracted from ``test_setup_handlers_discovery.py`` to keep that
file under the 300-line Python ceiling.  Covers the URL-shape rules
for discovery records (ip-first, host fallback, default port).
"""

from sethlans_worker_agent.web_ui.setup.handlers_discovery import (
    _build_manager_url,
)


class TestBuildManagerUrl:
    def test_uses_ip_first(self):
        url = _build_manager_url(
            {"ip": "10.0.0.1", "host": "h", "port": 8080},
        )
        assert url == "https://10.0.0.1:8080/api/"

    def test_falls_back_to_host(self):
        url = _build_manager_url(
            {"host": "lab.example", "port": 9090},
        )
        assert url == "https://lab.example:9090/api/"

    def test_default_port(self):
        url = _build_manager_url({"ip": "10.0.0.1"})
        assert url == "https://10.0.0.1:8080/api/"
