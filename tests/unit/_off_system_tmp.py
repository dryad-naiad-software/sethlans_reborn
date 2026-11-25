# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A ``tmp_path`` that the data-folder checks accept on every OS.

On macOS pytest's ``tmp_path`` resolves to ``/private/var/folders/...``,
which ``shared.data_folder`` refuses as a system folder. There the
fixture is a unique folder under ``~/.cache/sethlans-tests``
(``tests._home_scratch``), removed afterwards; elsewhere it is pytest's
own ``tmp_path``. A test module (or conftest) opts in with
``tmp_path = off_system_tmp_path_fixture()``.
"""
import sys

import pytest

from tests._home_scratch import scratch_folder


def off_system_tmp_path_fixture():
    @pytest.fixture
    def tmp_path(tmp_path):
        if sys.platform != "darwin":
            yield tmp_path
            return
        yield from scratch_folder()

    return tmp_path
