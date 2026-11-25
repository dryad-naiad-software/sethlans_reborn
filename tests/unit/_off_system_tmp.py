# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A ``tmp_path`` that the data-folder checks accept on every OS.

On macOS pytest's ``tmp_path`` resolves to ``/private/var/folders/...``,
which ``shared.data_folder`` refuses as a system folder. There the
fixture is a unique folder under the home folder, removed afterwards;
elsewhere it is pytest's own ``tmp_path``. A test module (or conftest)
opts in with ``tmp_path = off_system_tmp_path_fixture()``.
"""
import shutil
import sys
import uuid
from pathlib import Path

import pytest

HOME_SCRATCH = Path.home() / ".cache" / "sethlans-tests"


def _home_folder():
    folder = HOME_SCRATCH / uuid.uuid4().hex
    folder.mkdir(parents=True)
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        try:
            HOME_SCRATCH.rmdir()  # only when no other test still uses it
        except OSError:
            pass


def off_system_tmp_path_fixture():
    @pytest.fixture
    def tmp_path(tmp_path):
        if sys.platform != "darwin":
            yield tmp_path
            return
        yield from _home_folder()

    return tmp_path
