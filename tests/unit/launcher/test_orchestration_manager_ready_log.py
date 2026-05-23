# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""FR-LOG1: launcher emits a single INFO log line after cold-boot success.

Covers issue #205 / spec ``wizard_handoff_ux.md`` AC-4 and AC-5:

* AC-4 — After a successful cold-boot, ``launcher.log`` contains the
  line ``manager ready on https://localhost:8080/ — dashboard opening``
  at INFO level.
* AC-5 — On cold-boot health timeout, the line does NOT appear.

The supervision loop is short-circuited by mocking ``_all_live_exited``
to return ``True``, mirroring the pattern in
``tests/unit/launcher/test_orchestration_cold_boot.py``.
"""

from __future__ import annotations

import argparse
import json
import logging

from launcher import orchestration


_EXPECTED_LOG = "manager ready on https://localhost:8080/"


def _args_ns():
    return argparse.Namespace(no_browser=True, print_url=True)


def _write_topology(data_dir, topo):
    (data_dir / "topology.json").write_text(
        json.dumps({"topology": topo}), encoding="utf-8",
    )


def _common_normal_mode_mocks(mocker):
    mocker.patch.object(orchestration, "remove_setup_section")
    mocker.patch.object(orchestration, "start_caddy_supervisor")
    mocker.patch.object(
        orchestration, "_all_live_exited", return_value=True,
    )


class TestManagerReadyLogLine:

    def test_manager_ready_logged_after_cold_boot_success(
        self, mocker, tmp_path, caplog,
    ):
        """FR-LOG1 / AC-4: the INFO log line appears after
        ``_await_cold_boot`` returns ``None`` (success)."""
        _common_normal_mode_mocks(mocker)
        _write_topology(tmp_path, "manager")
        mocker.patch.object(
            orchestration, "wait_for_health", return_value=True,
        )
        mocker.patch.object(orchestration, "open_browser")
        manager_proc = mocker.MagicMock()

        with caplog.at_level(
            logging.INFO, logger="launcher.orchestration",
        ):
            rc = orchestration.run_normal_mode(
                tmp_path, _args_ns(), tray=None, secret="s",
                start_component=lambda *_a, **_k: manager_proc,
                on_cold_boot_ready=mocker.MagicMock(),
            )

        assert rc == 0
        matching = [
            r for r in caplog.records
            if r.levelno == logging.INFO and _EXPECTED_LOG in r.message
            and "dashboard opening" in r.message
        ]
        assert len(matching) == 1, (
            "Expected exactly one INFO record containing "
            f"{_EXPECTED_LOG!r}; got {[r.message for r in caplog.records]}"
        )

    def test_manager_ready_not_logged_on_health_timeout(
        self, mocker, tmp_path, caplog,
    ):
        """FR-LOG1 / AC-5: the log line MUST NOT appear when cold-boot
        fails (health probe times out)."""
        _common_normal_mode_mocks(mocker)
        _write_topology(tmp_path, "manager")
        mocker.patch.object(
            orchestration, "wait_for_health", return_value=False,
        )
        mocker.patch.object(orchestration, "open_browser")

        manager_proc = mocker.MagicMock()
        manager_proc.poll.return_value = None
        manager_proc.wait.return_value = 0

        with caplog.at_level(
            logging.INFO, logger="launcher.orchestration",
        ):
            rc = orchestration.run_normal_mode(
                tmp_path, _args_ns(), tray=None, secret="s",
                start_component=lambda *_a, **_k: manager_proc,
                on_cold_boot_ready=mocker.MagicMock(),
                on_startup_failed=mocker.MagicMock(),
            )

        assert rc == 1
        assert not any(
            _EXPECTED_LOG in r.message for r in caplog.records
        ), (
            "Log line must not appear on cold-boot timeout. "
            f"Logged: {[r.message for r in caplog.records]}"
        )
