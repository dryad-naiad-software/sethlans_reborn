# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The worker page's Waitress trusts Caddy's ``X-Forwarded-For`` from loopback only (setup-rebuild FR-SEC-3, FR-SEC-8).

``waitress.create_server`` and the server thread are replaced, so no socket
is opened and no thread runs.
"""
from sethlans_worker_agent.web_ui import server


def test_waitress_gets_the_three_trusted_proxy_settings(mocker, monkeypatch):
    """FR-SEC-3: without them every request looks like 127.0.0.1 and one LAN host locks everyone out."""
    monkeypatch.setattr(server.config, 'UI_ENABLED', True)
    monkeypatch.setattr(server, 'is_password_configured', lambda: True)
    monkeypatch.setattr(server, '_server', None)
    monkeypatch.setattr(server, '_server_thread', None)
    create = mocker.patch.object(server.waitress, 'create_server')
    mocker.patch.object(server.threading, 'Thread')

    server.start_server(None, None)

    kwargs = create.call_args.kwargs
    assert (kwargs['host'], kwargs['trusted_proxy'], kwargs['trusted_proxy_count'], kwargs['trusted_proxy_headers']) \
        == ('127.0.0.1', '127.0.0.1', 1, {'x-forwarded-for'})
