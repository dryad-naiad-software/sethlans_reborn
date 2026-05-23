# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit test for FR-LOOP6 / AC-16 (issue #203) early-quit check.

``run_normal_mode`` MUST return immediately (without spawning Caddy or
the manager) when ``supervision.get_quit_requested_event()`` is set at
its entry point. This protects against a wasted spawn cycle when the
user clicks Quit during the brief wizard-to-normal-mode transition
window of a fresh-install fall-through.
"""

from __future__ import annotations

import argparse
import json

import pytest

from launcher import orchestration, supervision


def _args_ns():
    return argparse.Namespace(no_browser=True, print_url=True)


def _write_topology(data_dir, topo="manager_worker"):
    (data_dir / "topology.json").write_text(
        json.dumps({"topology": topo}), encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _clear_quit_event():
    """Ensure no cross-test contamination from the process-wide event."""
    supervision.get_quit_requested_event().clear()
    yield
    supervision.get_quit_requested_event().clear()


class TestRunNormalModeEarlyQuit:

    def test_returns_zero_without_starting_caddy_when_quit_set(
        self, tmp_path, mocker,
    ):
        _write_topology(tmp_path)
        start_caddy = mocker.patch.object(
            orchestration, "start_caddy_supervisor",
        )
        mocker.patch.object(orchestration, "remove_setup_section")
        on_ready = mocker.MagicMock()
        start_component = mocker.MagicMock()

        supervision.get_quit_requested_event().set()

        rc = orchestration.run_normal_mode(
            tmp_path, _args_ns(), tray=None, secret="s",
            start_component=start_component,
            on_cold_boot_ready=on_ready,
        )

        assert rc == 0
        start_caddy.assert_not_called()
        start_component.assert_not_called()
        # The quit cleanup path fires on_cold_boot_ready so the splash
        # dismisses via the success path (mirrors wizard quit semantics).
        on_ready.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
