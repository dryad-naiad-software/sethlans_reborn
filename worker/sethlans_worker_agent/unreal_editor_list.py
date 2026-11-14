# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``run_worker.exe --list-unreal-editors [--output FILE]`` (issue #233).

UE 5.8's TraceLog always opens a TCP control listener on every interface
(port 1985, or a random fallback port), so Windows Firewall prompts once
per machine for ``UnrealEditor-Cmd.exe``. The Windows installer runs this
subcommand after installing the files and adds one inbound-allow rule per
line it prints, so an unattended render node never shows that prompt.

Each line is ``<rule name><TAB><absolute path of UnrealEditor-Cmd.exe>``
for one **supported** engine, found by the worker's own detection
(``unreal_executor.detection``) reading only the Epic launcher's
``LauncherInstalled.dat``; its path rules apply unchanged (fixed local
disks only, never a UNC path; launcher installs outside Program Files are
untrusted). The rule name is ``Sethlans Unreal Editor (<build>)``, with
`` #2``, `` #3`` ... when two installs share a build.

``--output FILE`` writes the lines atomically to ``FILE`` with CRLF line
ends, in the ANSI code page NSIS's ``FileRead`` decodes. A line that does
not fit that code page or the installer's string limit is left out (that
engine just keeps Windows' one-time prompt). Without ``--output`` the
lines go to stdout, for an administrator to inspect.

Read-only and quick: it never starts the agent, reads or writes worker
config, or contacts the manager, so it is safe to run elevated. Any
detection failure prints a note on stderr and yields no lines; the exit
code is 0 unless the arguments are wrong (2) or ``FILE`` cannot be
written (1). The installer adds no Unreal rules in every one of those
cases.
"""
import os
import sys
import tempfile
from typing import Iterable, List, Optional, Tuple

LIST_ARG = '--list-unreal-editors'
OUTPUT_OPT = '--output'
RULE_PREFIX = 'Sethlans Unreal Editor'
# NSIS_MAX_STRLEN is 1024 bytes and the whole netsh command (about 160
# bytes plus the line) must fit in it.
MAX_LINE_BYTES = 700
USAGE = f"usage: run_worker {LIST_ARG} [{OUTPUT_OPT} FILE]"
_FORBIDDEN_CHARS = frozenset('"\t\r\n')

Rule = Tuple[str, str]


def rule_name(build: str, ordinal: int = 1) -> str:
    """Firewall rule name for the ``ordinal``-th install of ``build``."""
    base = f"{RULE_PREFIX} ({build})"
    return base if ordinal == 1 else f"{base} #{ordinal}"


def _listable(path: str) -> bool:
    """Absolute ``X:\\`` path with nothing that could break a netsh argument."""
    from .unreal_executor import detection
    return detection.is_local_drive_path(path) and not (_FORBIDDEN_CHARS & set(path))


def editor_rules(installs: Iterable) -> List[Rule]:
    """``(rule name, editor path)`` for each supported install, in order."""
    from shared.unreal.builds import is_supported
    rules = []
    seen = {}
    for install in installs:
        if not install.supported or not is_supported(install.build):
            continue
        path = str(install.editor_cmd)
        if not _listable(path):
            continue
        seen[install.build] = seen.get(install.build, 0) + 1
        rules.append((rule_name(install.build, seen[install.build]), path))
    return rules


def format_line(rule: Rule) -> str:
    return f"{rule[0]}\t{rule[1]}"


def _file_encoding() -> str:
    # NSIS FileRead decodes with the system ANSI code page (in ANSI and
    # Unicode installers alike); 'mbcs' is that code page in Python.
    return 'mbcs' if sys.platform == 'win32' else 'utf-8'


def encode_for_installer(rules: Iterable[Rule]) -> bytes:
    """The ``--output`` file body; lines the installer cannot use are dropped."""
    encoding = _file_encoding()
    lines = []
    for rule in rules:
        line = format_line(rule)
        try:
            data = line.encode(encoding, 'strict')
        except UnicodeError:
            _note(f"skipping {rule[1]!r}: not representable in the {encoding} code page")
            continue
        if len(data) > MAX_LINE_BYTES:
            _note(f"skipping {rule[1]!r}: path too long for the installer")
            continue
        lines.append(data + b'\r\n')
    return b''.join(lines)


def _note(message: str) -> None:
    stream = sys.stderr
    if stream is not None:  # None in a windowed (console=False) frozen exe
        try:
            stream.write(f"{message}\n")
        except (OSError, ValueError):
            pass


def _detect() -> list:
    from .unreal_executor import detection
    return detection.detect_engines([detection.WindowsLauncherLocator()])


def _write_atomic(path: str, data: bytes) -> int:
    directory = os.path.dirname(os.path.abspath(path))
    try:
        fd, tmp = tempfile.mkstemp(dir=directory, prefix='.unreal-editors-', suffix='.tmp')
    except OSError as exc:
        _note(f"cannot write {path}: {exc}")
        return 1
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
        os.replace(tmp, path)
    except OSError as exc:
        _note(f"cannot write {path}: {exc}")
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return 1
    return 0


def _parse_args(args: List[str]) -> Tuple[bool, Optional[str]]:
    """``(ok, output path or None)``."""
    if not args:
        return True, None
    if len(args) == 2 and args[0] == OUTPUT_OPT and args[1]:
        return True, args[1]
    return False, None


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point; ``argv`` is everything after ``--list-unreal-editors``."""
    ok, output = _parse_args(list(sys.argv[2:] if argv is None else argv))
    if not ok:
        _note(USAGE)
        return 2
    try:
        rules = editor_rules(_detect())
    except Exception as exc:  # noqa: BLE001 -- the installer must never fail on this
        _note(f"Unreal Engine detection failed: {type(exc).__name__}: {exc}")
        rules = []
    if output is not None:
        return _write_atomic(output, encode_for_installer(rules))
    if sys.stdout is not None:
        for rule in rules:
            sys.stdout.write(format_line(rule) + '\n')
        sys.stdout.flush()
    return 0
