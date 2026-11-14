# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Windows path strings are handled with ``ntpath`` on every platform (CI on Linux and macOS)."""
import ntpath
import os

import pytest

from sethlans_worker_agent.unreal_executor import winpath


@pytest.mark.parametrize('path', ['D:\\uecache', 'd:\\', 'C:/UE/Engine', 'C:\\Program Files\\Epic Games'])
def test_drive_paths_are_windows_style(path):
    assert winpath.is_windows_style(path) and winpath.module_for(path) is ntpath


@pytest.mark.parametrize('path', ['/tmp/x', 'relative\\x', 'D:', 'uecache'])
def test_other_paths_use_the_platform_module(path):
    assert not winpath.is_windows_style(path) and winpath.module_for(path) is os.path


def test_join_normcase_normpath_are_ntpath_for_drive_paths():
    assert winpath.join('D:\\uecache', 'zen', 'data') == 'D:\\uecache\\zen\\data'
    assert winpath.normcase('C:/UE/Engine/zenserver.EXE') == 'c:\\ue\\engine\\zenserver.exe'
    assert winpath.normpath('D:\\uecache\\j\\..\\zen\\') == 'D:\\uecache\\zen'
