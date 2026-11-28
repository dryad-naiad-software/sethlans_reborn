# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The processes and environment of ``tools/setup_smoke.py``.

:class:`ChildTracker` starts every process the smoke runs (the setup
process, and one short command at a time) in its own process group and
remembers it, so :meth:`ChildTracker.stop` (also from the watchdog) ends
whatever is still running, with its whole process tree.
:func:`child_env` points every per-user and temp folder a child could
write into the smoke's scratch folder.
"""

from __future__ import annotations

import os
import pathlib
import re
import socket
import subprocess
import sys
from typing import Mapping, Optional

from _smoke_common import terminate

from shared import owner_only_acl

# XDG base folders (XDG_DATA_HOME, XDG_CONFIG_HOME, XDG_CACHE_HOME, ...).
_XDG_HOME = re.compile(r"XDG_\w+_HOME")
REAP_SECONDS = 5


class SmokeFailure(Exception):
    """One smoke step failed; the message says which and why."""


def group_flags(platform: str = sys.platform) -> dict:
    """Popen arguments that start a child in its own process group."""
    if platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def free_ports(count: int) -> list[int]:
    """``count`` distinct free loopback ports."""
    ports: set[int] = set()
    while len(ports) < count:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            ports.add(sock.getsockname()[1])
    return sorted(ports)


def scratch_folders(root: pathlib.Path) -> dict[str, pathlib.Path]:
    """The per-user and temp folders of a child, all under ``root``."""
    return {name: root / name for name in ("home", "localappdata", "appdata", "tmp")}


def child_env(base: Mapping[str, str], root: pathlib.Path, settings: pathlib.Path,
              ports: Mapping[str, int]) -> dict:
    """The environment of every child: ``base`` without its ``SETHLANS_*``,
    ``DJANGO_SETTINGS_MODULE`` and ``XDG_*_HOME``, with the settings, the
    per-user folders and the temp folder under ``root``, and the ports."""
    env = {key: value for key, value in base.items()
           if not key.upper().startswith("SETHLANS_") and key.upper() != "DJANGO_SETTINGS_MODULE"
           and not _XDG_HOME.fullmatch(key.upper())}
    folders = scratch_folders(root)
    env.update({
        "SETHLANS_SETTINGS_DIR": str(settings),
        "HOME": str(folders["home"]),
        "USERPROFILE": str(folders["home"]),
        "LOCALAPPDATA": str(folders["localappdata"]),
        "APPDATA": str(folders["appdata"]),
        "TEMP": str(folders["tmp"]),
        "TMP": str(folders["tmp"]),
        "TMPDIR": str(folders["tmp"]),
        "SETHLANS_MANAGER_WAITRESS_PORT_PUBLIC": str(ports["public"]),
        "SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL": str(ports["internal"]),
        "SETHLANS_SETUP_PORT": str(ports["setup"]),
        "PYTHONUNBUFFERED": "1",
    })
    return env


def write_token(settings: pathlib.Path, token: str) -> pathlib.Path:
    """A fresh owner-only ``<settings>/setup/setup_token``, as the launcher writes it."""
    for folder in (settings, settings / "setup"):
        owner_only_acl.create_owner_only_dir(folder)
    path = settings / "setup" / "setup_token"
    fd = owner_only_acl.create_owner_only_file(path)
    try:
        os.write(fd, token.encode("ascii"))
    finally:
        os.close(fd)
    return path


class ChildTracker:
    """Every process the smoke starts, so each one can be ended."""

    def __init__(self, env: dict):
        self.env = env
        self.proc: Optional[subprocess.Popen] = None   # the setup process
        self.child: Optional[subprocess.Popen] = None  # the short command now running

    def start(self, argv: list[str], stdout, stderr) -> subprocess.Popen:
        """Start the long-running setup process (output to the given files)."""
        self.proc = subprocess.Popen(argv, env=self.env, stdin=subprocess.DEVNULL,
                                     stdout=stdout, stderr=stderr, **group_flags())
        return self.proc

    def run(self, argv: list[str], stdin: bytes, timeout: float) -> tuple[int, bytes, bytes]:
        """Run one short command; ``(exit code, stdout, stderr)``.

        Past ``timeout`` its process tree is ended and :class:`SmokeFailure` raised.
        """
        child = subprocess.Popen(argv, env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, **group_flags())
        self.child = child
        try:
            out, errs = child.communicate(stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate(child)
            try:
                child.communicate(timeout=REAP_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            raise SmokeFailure(f"{' '.join(argv)} did not finish within {timeout:.0f}s") from None
        finally:
            self.child = None
        return child.returncode, out, errs

    def stop(self) -> None:
        """End the short command and the setup process, each with its tree, if still running."""
        for proc in (self.child, self.proc):
            if proc is not None:
                terminate(proc)
