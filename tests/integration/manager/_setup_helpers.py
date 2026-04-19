# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the setup-auth-unification integration test suite.

Centralises data_dir patching + manager.ini seeding so every test file
exercises the same end-to-end request pipeline configuration.
"""

from __future__ import annotations

import configparser
import json

VALID_TOKEN = "t" * 64


def patch_data_dir(mocker, tmp_path, token: str = VALID_TOKEN):
    """Patch every setup view's data_dir resolver to ``tmp_path``.

    Also writes a ``manager.ini`` with ``[setup] token=<token>`` so the
    bootstrap endpoint reads the same token callers send.
    """
    from sethlans_manager.middleware import setup_gate
    from workers.views import setup_bootstrap as bootstrap_mod
    from workers.views import setup_restart as restart_mod
    from workers.views import setup_status as status_mod
    from workers.views import setup_accounts as accounts_mod
    from workers.views import setup_verify as verify_mod

    mocker.patch.object(bootstrap_mod, "_data_dir", return_value=tmp_path)
    mocker.patch.object(restart_mod, "_data_dir", return_value=tmp_path)
    mocker.patch.object(status_mod, "_get_data_dir", return_value=tmp_path)
    mocker.patch.object(accounts_mod, "_get_data_dir", return_value=tmp_path)
    mocker.patch.object(verify_mod, "_get_data_dir", return_value=tmp_path)
    mocker.patch.object(setup_gate, "_get_data_dir", return_value=tmp_path)
    mocker.patch(
        "workers.services.setup_token._data_dir", return_value=tmp_path,
    )

    if token is not None:
        ini = tmp_path / "manager.ini"
        cfg = configparser.ConfigParser()
        cfg.add_section("setup")
        cfg.set("setup", "token", token)
        with open(ini, "w") as f:
            cfg.write(f)
    return tmp_path


def enter_setup_mode(mocker):
    """Flip ``SetupGateMiddleware`` into setup-incomplete mode."""
    from sethlans_manager.middleware import setup_gate
    setup_gate._setup_complete = False
    mocker.patch.object(setup_gate, "_check_sentinel", return_value=False)


def exit_setup_mode():
    """Restore ``_setup_complete = True`` for post-test cleanup."""
    from sethlans_manager.middleware import setup_gate
    setup_gate._setup_complete = True


def reset_rate_limiter(mocker):
    """Install a fresh bootstrap rate limiter (clean counters per test)."""
    from workers.views import setup_bootstrap as bootstrap_mod
    from workers.rate_limiter import InMemoryRateLimiter
    limiter = InMemoryRateLimiter(max_attempts=10, window_seconds=300)
    mocker.patch.object(
        bootstrap_mod, "_bootstrap_rate_limiter", limiter,
    )
    return limiter


def post_json(client, path, payload, **extra):
    return client.post(
        path, data=json.dumps(payload),
        content_type="application/json", **extra,
    )


def bootstrap(client, token: str = VALID_TOKEN):
    """Perform bootstrap; return the response."""
    return post_json(client, "/api/setup/bootstrap/", {"token": token})


def write_sentinel_complete(tmp_path, topology: str = "manager"):
    """Write a completed sentinel (setup fully done)."""
    from workers.services.sentinel import create_sentinel
    create_sentinel(tmp_path, topology, ["topology_chosen", "verified"])
