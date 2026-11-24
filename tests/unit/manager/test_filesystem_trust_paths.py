# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where ``filesystem_trust`` writes the local worker's ``config.json``.

The worker's identity folder (:func:`shared.frozen_paths.get_identity_dir`):
``SETHLANS_WORKER_DATA_DIR`` when set, else ``<settings folder>/worker``.
It never moves with the data folder.
"""

import pytest

from workers.services.filesystem_trust import get_worker_config_path


@pytest.fixture
def settings_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("SETHLANS_WORKER_DATA_DIR", raising=False)
    monkeypatch.delenv("SETHLANS_DATA_ROOT", raising=False)
    folder = tmp_path / "settings"
    monkeypatch.setenv("SETHLANS_SETTINGS_DIR", str(folder))
    return folder


def test_default_is_settings_folder_worker(settings_dir):
    assert get_worker_config_path() == settings_dir / "worker" / "config.json"


def test_data_folder_never_moves_it(settings_dir, tmp_path, monkeypatch):
    """``SETHLANS_DATA_ROOT`` and ``[paths] data_dir`` move data, not identity."""
    monkeypatch.setenv("SETHLANS_DATA_ROOT", str(tmp_path / "bulk"))
    ini = settings_dir / "sethlans.ini"
    ini.parent.mkdir(parents=True)
    ini.write_text(f"[paths]\ndata_dir = {tmp_path / 'other'}\n", encoding="utf-8")
    monkeypatch.setenv("SETHLANS_SETTINGS_FILE", str(ini))

    assert get_worker_config_path() == settings_dir / "worker" / "config.json"


def test_same_file_as_the_worker_agent(settings_dir):
    """The manager writes the file the worker reads (one shared resolver)."""
    from sethlans_worker_agent.config_store import paths as worker_paths
    assert get_worker_config_path() == worker_paths.user_config_path()
