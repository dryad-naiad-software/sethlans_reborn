# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Path-string joins for UE command lines and environments.

A ``CACHE_DIR`` such as ``D:\uecache`` is joined with ``ntpath`` on every
platform, so the strings the worker builds for UE (and the unit tests'
golden values) are identical on the Linux and macOS CI runners; any other
path uses ``os.path``.
"""
import ntpath
import os
import re

_WIN_DRIVE = re.compile(r'^[A-Za-z]:[\/]', re.ASCII)


def is_windows_style(path) -> bool:
    return bool(_WIN_DRIVE.match(str(path)))


def module_for(path):
    return ntpath if is_windows_style(path) else os.path


def join(base, *parts) -> str:
    text = str(base)
    return module_for(text).join(text, *parts)
