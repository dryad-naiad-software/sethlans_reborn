# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``launcher.setup_helpers.remove_setup_section``.

It stays for the current loop until chunk 6b; the wizard-era ``[setup]``
keys go and the setup-finished keys stay (FR-SF-4).
"""

import configparser

from launcher.setup_helpers import remove_setup_section


def test_remove_on_missing_file_creates_nothing(tmp_path):
    remove_setup_section(tmp_path / "sethlans.ini")
    assert not (tmp_path / "sethlans.ini").exists()


def test_remove_keeps_setup_finished_keys(tmp_path):
    ini_path = tmp_path / "sethlans.ini"
    ini_path.write_text(
        "[setup]\ntoken = t\nsession_id = s\nversion = 1\nmode = manager\n"
        "completed_at = 2026-11-24T10:00:00+00:00\n",
        encoding="utf-8",
    )
    remove_setup_section(ini_path)
    config = configparser.RawConfigParser(interpolation=None)
    config.read(ini_path)
    assert dict(config["setup"]) == {
        "version": "1", "mode": "manager", "completed_at": "2026-11-24T10:00:00+00:00",
    }


def test_remove_drops_section_left_empty(tmp_path):
    ini_path = tmp_path / "sethlans.ini"
    ini_path.write_text("[setup]\ntoken = t\nsession_id = s\n[server]\nport = 1\n", encoding="utf-8")
    remove_setup_section(ini_path)
    config = configparser.RawConfigParser(interpolation=None)
    config.read(ini_path)
    assert config.sections() == ["server"]
