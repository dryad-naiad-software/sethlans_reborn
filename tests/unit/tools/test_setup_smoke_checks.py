# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The pure checks of the frozen setup-mode smoke (``tools/_setup_smoke_checks.py``).

The smoke is the build-time guard of setup-rebuild chunk 7a: setup mode
answers health and the page, a manager-mode submit leaves a correct
``sethlans.ini`` (FR-SM-4) with no plain password, and
``run_worker --setup-cards`` keeps its one-line JSON contract (FR-WK-1).
These tests pin that each check passes a good answer and names a bad one.
"""
import datetime
import json

import pytest

import _setup_smoke_checks as checks
import setup_smoke

NOW = datetime.datetime(2026, 11, 28, 12, 0, tzinfo=datetime.timezone.utc)
GOOD_INI = """\
[setup]
version = 1
mode = manager
completed_at = 2026-11-28T11:59:00+00:00

[first_admin]
username = smokeadmin
password_hash = pbkdf2_sha256$1000000$salt$hash
"""
GOOD_CARD = {
    "address": "0000:23:00.0", "key": "pci:0000:23:00.0", "pci": "23:00.0", "name": "Card",
    "brand": "AMD", "vendor_id": 4098, "device_id": 29772, "revision_id": None,
    "screen": True, "primary_screen": False,
}
GOOD_CARDS = {
    "os_probe_failed": False, "cards": [GOOD_CARD],
    "saved": {"compute": "cpu", "cards": [], "combine": False, "env_override": False,
              "config_read_failed": False},
    "unreal_enabled": False, "unreal_enabled_env": False, "enrollment": None,
    "ui_password_set": False,
}


def _line(answer) -> bytes:
    return (json.dumps(answer) + "\n").encode("ascii")


@pytest.mark.parametrize("environ, platform, expected", [
    ({"RUNNER_TEMP": "/runner/_temp", "TMPDIR": "/private/var/x"}, "darwin", "/runner/_temp"),
    ({"TMPDIR": "/private/var/x"}, "darwin", "/tmp"),
])
def test_smoke_parent_avoids_macos_tmpdir(environ, platform, expected):
    """7a finding 4: $TMPDIR (/private/var) is refused as a data folder; $RUNNER_TEMP first."""
    assert str(checks.smoke_parent(environ, platform)).replace("\\", "/") == expected


def test_smoke_parent_uses_system_temp_elsewhere(mocker):
    mocker.patch.object(checks.tempfile, "gettempdir", return_value="/sys/temp")
    assert str(checks.smoke_parent({}, "linux")).replace("\\", "/") == "/sys/temp"


@pytest.mark.parametrize("status, payload, problem", [
    (200, {"mode": "setup", "boot_id": "b1"}, None),
    (200, {"mode": "manager", "boot_id": "b1"}, "not 'setup'"),
    (200, {"mode": "setup"}, "no boot_id"),
    (0, None, "not 200"),
])
def test_health_problems(status, payload, problem):
    """Smoke step 2: setup-mode health is ``mode: setup`` with a ``boot_id``."""
    found = checks.health_problems(status, payload)
    assert (found == []) if problem is None else (problem in " ".join(found))


def test_page_problems_accepts_html_and_names_a_503():
    """Smoke step 3: ``GET /setup/`` is 200 html (a missing front-end build answers 503)."""
    assert checks.page_problems(200, {"content-type": "text/html; charset=utf-8"},
                                b"<!doctype html><html lang=en>") == []
    found = checks.page_problems(503, {"content-type": "text/plain"}, b"not built")
    assert len(found) == 3


def test_ini_problems_accepts_a_saved_manager_setup():
    """FR-SM-4: [setup] version 1, mode manager, a completed_at, a pbkdf2 admin hash."""
    assert checks.ini_problems(GOOD_INI, NOW) == []


@pytest.mark.parametrize("old, new, problem", [
    ("version = 1", "version = 2", "version"),
    ("mode = manager", "mode = worker", "mode"),
    ("completed_at = 2026-11-28T11:59:00+00:00", "completed_at = yesterday", "not an ISO"),
    ("completed_at = 2026-11-28T11:59:00+00:00", "completed_at = 2026-11-28T11:59:00", "time zone"),
    ("completed_at = 2026-11-28T11:59:00+00:00", "completed_at = 2026-11-27T11:59:00+00:00", "not from this run"),
    ("completed_at = 2026-11-28T11:59:00+00:00\n", "", "no completed_at"),
    ("pbkdf2_sha256$1000000$salt$hash", "Plain-Password", "password_hash"),
])
def test_ini_problems_names_each_bad_field(old, new, problem):
    found = checks.ini_problems(GOOD_INI.replace(old, new), NOW)
    assert len(found) == 1 and problem in found[0], found


def test_files_holding_finds_the_secret_in_nested_files(tmp_path):
    """Smoke step 4: the plain admin password must be in no file (logs included)."""
    (tmp_path / "settings" / "logs").mkdir(parents=True)
    leak = tmp_path / "settings" / "logs" / "setup-mode.log"
    leak.write_bytes(b"... Plain-Secret ...")
    (tmp_path / "settings" / "sethlans.ini").write_bytes(b"password_hash = pbkdf2_sha256$x")
    assert checks.files_holding(tmp_path, b"Plain-Secret") == [leak]


@pytest.mark.parametrize("answer", [GOOD_CARDS, {**GOOD_CARDS, "cards": []}, {
    **GOOD_CARDS, "enrollment": {"enrolled": True, "manager_id": "m", "manager_name": None,
                                 "host": "farm", "port": 8080}}])
def test_cards_problems_accepts_the_documented_shape(answer):
    """FR-WK-1: one JSON line in the cards_cmd shape; no card count is asserted."""
    assert checks.cards_problems(_line(answer)) == []


@pytest.mark.parametrize("stdout, problem", [
    (_line(GOOD_CARDS) * 2, "2 lines"),
    (b"not json\n", "not JSON"),
    (_line({k: v for k, v in GOOD_CARDS.items() if k != "ui_password_set"}), "keys"),
    (_line({**GOOD_CARDS, "os_probe_failed": "no"}), "answer.os_probe_failed"),
    (_line({**GOOD_CARDS, "cards": [{**GOOD_CARD, "vendor_id": True}]}), "cards[0].vendor_id"),
    (_line({**GOOD_CARDS, "saved": {**GOOD_CARDS["saved"], "cards": [7]}}), "saved.cards[0]"),
    (_line({**GOOD_CARDS, "enrollment": {"enrolled": True}}), "enrollment keys"),
])
def test_cards_problems_names_a_broken_answer(stdout, problem):
    found = checks.cards_problems(stdout)
    assert problem in " ".join(found), found


def test_component_cmd_fails_loudly_for_a_missing_executable(tmp_path):
    """A build that lost a component fails the smoke, never skips it."""
    with pytest.raises(setup_smoke.SmokeFailure, match="manager executable not found"):
        setup_smoke.component_cmd(tmp_path, "manager")
