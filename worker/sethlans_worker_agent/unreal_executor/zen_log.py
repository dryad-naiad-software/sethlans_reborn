# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Zen log verification (Unreal foundation FR-UE-26 "Verification", Dom R3-8).

Each phase log is scanned to EOF after exit confirmation:

* every ``Found command line override ZenDataPath=<p>`` line must name
  ``CACHE_DIR\\zen\\data``, and so must the ``data dir '...'`` and
  ``--data-dir`` of zenserver's launch line;
* the ``Skipping command line override ZenDataPath=`` warning (UE refused
  the path) fails;
* ``Found existing instance running on port <n> with different data
  directory`` (``:4210``) fails: the farm launch would shut that zenserver
  down (review D6);
* every Zen line that names a port (``--port <n>`` on the launch line,
  ``... on port <n> ...``) must name ``ZEN_PORT``;
* when ``require`` is set, the log must contain the override line **and**
  a launch or attach line on ``ZEN_PORT``.

5.8.4 source lines (chunk 7 integration requirement 14), from
``Engine/Source/Developer/Zen/Private/ZenServerInterface.cpp``:

* override: ``:1024`` ``"Found command line override ZenDataPath=%ls"``
  (``Log``); refusal ``:1027`` ``"Skipping command line override ..."``;
* launch: ``:2231`` ``"Launching executable '%ls', working dir '%ls', data
  dir '%ls', args '%ls'"`` (``Display``), where args start with
  ``"--port %d --data-dir %s"`` (``:2044``);
* attach: ``:4168`` ``"Found existing instance running on port %u matching
  our settings, no actions needed"`` and ``:4194`` ``"... when not using
  shared context, will use it"``.

**Presence in prep and warm-up.** The spikes found the override line in
every log, commandlet prep and editor warm-up included ("Setup common to
all spikes"), so ``REQUIRED_PHASES`` covers every phase: the FR-UE-26
exemption for prep and warm-up does not apply.
"""
import os
import re
from dataclasses import dataclass
from typing import Iterable, Optional

from sethlans_worker_agent.unreal_executor import zen_private
from sethlans_worker_agent.unreal_executor.log_tail import MB, iter_log_lines

MAX_LINE_CHARS = 4096  # each Zen line is checked in its first 4096 characters (Sec S3)

FAILED_MESSAGE = "Unreal did not use the farm-private Zen data path; refusing to continue."
REQUIRED_PHASES = frozenset({'prep', 'warmup', 'render'})

_ZEN = 'LogZenServiceInstance:'
_OVERRIDE = re.compile(r'LogZenServiceInstance: (?:\w+: )?Found command line override ZenDataPath=(.*)$')
_SKIPPED = re.compile(r'LogZenServiceInstance: (?:\w+: )?Skipping command line override ZenDataPath=')
_LAUNCH = re.compile(r"LogZenServiceInstance: (?:\w+: )?Launching executable '[^']*', working dir '[^']*', "
                     r"data dir '([^']*)', args '([^']*)'")
_OTHER_DATA_DIR = re.compile(r'LogZenServiceInstance: (?:\w+: )?Found existing instance running on port \d+ '
                             r'with different data directory')
_ATTACH = re.compile(r'LogZenServiceInstance: (?:\w+: )?Found existing instance running on port (\d+) '
                     r'(?:matching our settings|when not using shared context)')
_ARG_PORT = re.compile(r'--port[ =](\d+)')
_ARG_DATA = re.compile(r'--data-dir[ =]("[^"]*"|\S+)')
_ANY_PORT = re.compile(r'\bport (\d+)')


@dataclass
class ZenLogResult:
    ok: bool = True
    detail: str = ''
    override_seen: bool = False
    port_seen: bool = False
    zen_lines: int = 0

    @property
    def summary(self) -> str:
        return 'zen=FAILED' if not self.ok else ''

    @property
    def message(self) -> str:
        return '' if self.ok else FAILED_MESSAGE


def _norm(path: str) -> str:
    text = path.strip().strip('"').strip("'").replace('/', '\\')
    return os.path.normcase(os.path.normpath(text)) if text else ''


class _Scan:
    def __init__(self, expected_data: str, port: int):
        self.expected = _norm(expected_data)
        self.port = int(port)
        self.result = ZenLogResult()

    def fail(self, detail: str) -> None:
        if self.result.ok:
            self.result.ok, self.result.detail = False, detail

    def _check_path(self, value: str, where: str) -> None:
        if _norm(value) != self.expected:
            self.fail(f"{where} names Zen data path {value.strip()[:200]!r}")

    def _check_port(self, value: str, where: str) -> bool:
        if int(value) != self.port:
            self.fail(f"{where} names port {value}")
            return False
        return True

    def _launch(self, match) -> None:
        self._check_path(match.group(1), 'the zenserver launch line')
        args = match.group(2)
        data = _ARG_DATA.search(args)
        if data:
            self._check_path(data.group(1), 'the zenserver --data-dir')
        port = _ARG_PORT.search(args)
        if port is None:
            self.fail("the zenserver launch line has no --port")
        elif self._check_port(port.group(1), 'the zenserver launch line'):
            self.result.port_seen = True

    def line(self, text: str) -> None:
        if _ZEN not in text:
            return
        text = text[:MAX_LINE_CHARS]
        self.result.zen_lines += 1
        if _OTHER_DATA_DIR.search(text):  # review D6: a farm launch would shut another zenserver down
            self.fail("a zenserver with another data directory held the farm port")
            return
        override = _OVERRIDE.search(text)
        if override:
            self.result.override_seen = True
            self._check_path(override.group(1), 'the ZenDataPath override line')
            return
        if _SKIPPED.search(text):
            self.fail("Unreal skipped the ZenDataPath override")
            return
        launch = _LAUNCH.search(text)
        if launch:
            self._launch(launch)
            return
        attach = _ATTACH.search(text)
        if attach and self._check_port(attach.group(1), 'the zenserver attach line'):
            self.result.port_seen = True
            return
        for port in _ANY_PORT.findall(text):
            self._check_port(port, 'a Zen line')


def verify_lines(lines: Iterable[str], cache_dir, port: int, require: bool = True) -> ZenLogResult:
    """Apply the FR-UE-26 rules to an iterable of log lines."""
    scan = _Scan(str(zen_private.zen_data_dir(cache_dir)), port)
    for text in lines:
        scan.line(text.rstrip('\r\n'))
    result = scan.result
    if result.ok and require and not result.override_seen:
        scan.fail("the log has no 'Found command line override ZenDataPath=' line")
    if result.ok and require and not result.port_seen:
        scan.fail(f"the log shows no zenserver launch or attach on port {int(port)}")
    return result


def verify_log(path, cache_dir, port: int, phase: str, require: Optional[bool] = None,
               max_bytes: Optional[int] = None) -> ZenLogResult:
    """``verify_lines`` over a whole log (to EOF, bounded lines). A missing log fails when required.

    A log larger than ``max_bytes`` (default ``LOG_MAX_MB``) is not read and fails (log flood).
    """
    required = phase in REQUIRED_PHASES if require is None else require
    if max_bytes is None:
        from sethlans_worker_agent import config_unreal
        max_bytes = config_unreal.LOG_MAX_MB * MB
    try:
        if os.stat(path).st_size > max_bytes:
            return ZenLogResult(ok=False, detail=f"the {phase} log is larger than {max_bytes // MB} MB (log flood)")
        return verify_lines(iter_log_lines(path), cache_dir, port, require=required)
    except OSError as exc:
        result = ZenLogResult()
        if required:
            result.ok, result.detail = False, f"the {phase} log could not be read ({exc})"
        return result
