# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :mod:`launcher.setup_port` (FR-LN-3)."""

from __future__ import annotations

import pytest

from launcher import setup_port

OWN = (8088, 8089, 8090)


def _ini(settings_dir, port):
    ini = settings_dir / "sethlans.ini"
    if port is not None:
        settings_dir.mkdir(parents=True, exist_ok=True)
        ini.write_text(f"[server]\nport = {port}\n", encoding="utf-8")
    return ini


@pytest.mark.parametrize("earlier, busy, held, expected", [
    pytest.param(None, set(), None, 8080, id="no-earlier-port-8080"),
    pytest.param("9443", set(), None, 9443, id="earlier-port-kept"),
    pytest.param("9443", {9443, 8080, 8081}, None, 8082, id="earlier-busy-first-free-in-range"),
    pytest.param(None, {8080, 8081}, 8081, 8081, id="our-own-caddy-port-counts-free"),
    pytest.param("8088", set(), None, 8080, id="earlier-port-is-ours-ignored"),
    pytest.param("not-a-port", set(), None, 8080, id="unusable-earlier-value"),
])
def test_choose_setup_port(settings_dir, earlier, busy, held, expected):
    """FR-LN-3: the earlier ``[server] port``, else 8080; when taken, the first
    free of 8080-8089; a port our running Caddy holds counts as free."""
    ini = _ini(settings_dir, earlier)

    port = setup_port.choose_setup_port(ini, reserved=OWN, held_by_us=held, is_free=lambda p: p not in busy)

    assert port == expected


def test_own_loopback_ports_are_never_chosen_and_none_free_is_an_error(settings_dir):
    """FR-LN-3 (chunk 6 plan): 8088/8089 are setup mode's own loopback ports,
    skipped even when free; with nothing else free the choice fails loudly."""
    ini = _ini(settings_dir, None)
    with pytest.raises(setup_port.SetupPortError):
        setup_port.choose_setup_port(ini, reserved=OWN, is_free=lambda p: p >= 8088)
