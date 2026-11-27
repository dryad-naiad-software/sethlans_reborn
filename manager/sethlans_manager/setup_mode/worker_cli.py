# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Runs the worker program's one-shot setup commands (setup-rebuild FR-WK-1, FR-WK-2, FR-SEC-6).

``run_worker --setup-cards | --setup-discover | --setup-apply`` take one
JSON object on stdin and print one on stdout; the worker's own contract is
in ``worker/sethlans_worker_agent/setup_cli/__init__.py``. Here:

* :func:`worker_command` finds the program: frozen, the sibling bundle
  ``<install root>/worker/run_worker(.exe)``
  (``shared.frozen_paths.get_worker_dir``, the same install root every
  component resolves; ``launcher.component_paths`` is not in the manager
  bundle and computes ``bin/`` from the launcher's own place); from
  source, this interpreter and ``worker/run_worker.py``.
* :func:`child_env` is this process's environment without the setup and
  admin secrets, the database and security settings, any ``*_FILE``
  variable (except ``SETHLANS_SETTINGS_FILE``, passed on so the worker
  finds the same settings folder) and the worker's own password and key
  variables.
* :func:`run` sends the payload on stdin only, waits at most ``timeout``
  seconds (the whole process tree is killed after that), and logs the
  command, the exit code and the time taken, never stdin, stdout or
  stderr (stderr is not even read).

Every call returns a :class:`CliResult`: ``ok`` with the answer (exit 0),
``refused`` with the worker's ``error`` code and ``message`` (exit 1), or
``broken`` (exit 2, another exit code, a timeout, a program that could
not start, or not exactly one JSON object on stdout), which setup turns
into a 500-class answer (FR-WZ-2). The worker's ``message`` is kept as
printable text of at most ``MAX_MESSAGE`` characters (``clean.clean_text``).
"""
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import psutil
from django.views.decorators.debug import sensitive_variables

from shared.frozen_paths import get_worker_dir, is_frozen

from sethlans_manager.setup_mode.clean import clean_text

logger = logging.getLogger(__name__)

CARDS = "--setup-cards"
DISCOVER = "--setup-discover"
APPLY = "--setup-apply"
COMMANDS = (CARDS, DISCOVER, APPLY)

CARDS_TIMEOUT = 20.0
APPLY_TIMEOUT = 30.0
DISCOVER_SECONDS = 5
DISCOVER_MARGIN = 5.0
KILL_WAIT = 5.0
MAX_ANSWER_BYTES = 1024 * 1024
MAX_MESSAGE = 300

# Broken outcomes (``CliResult.error`` when ``broken``).
UNAVAILABLE = "worker_unavailable"
TIMED_OUT = "worker_timeout"
BAD_ANSWER = "worker_bad_answer"
FAILED = "worker_failed"

STRIPPED_PREFIXES = ("SETHLANS_SETUP_", "SETHLANS_ADMIN_", "SETHLANS_DB_", "SETHLANS_DATABASE_",
                     "SETHLANS_SECURITY_")
STRIPPED_NAMES = frozenset({"SETHLANS_WORKER_UI_PASSWORD", "SETHLANS_WORKER_ENROLLMENT_KEY",
                            "DJANGO_SETTINGS_MODULE"})
KEPT_FILE_NAMES = frozenset({"SETHLANS_SETTINGS_FILE"})
DATA_ROOT_ENV = "SETHLANS_DATA_ROOT"


@dataclass
class CliResult:
    command: str
    answer: dict = field(default_factory=dict)
    error: Optional[str] = None
    message: str = ""
    broken: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None


def worker_command() -> List[str]:
    """The argv prefix that starts the worker program (module doc)."""
    if is_frozen():
        name = "run_worker.exe" if sys.platform == "win32" else "run_worker"
        return [str(get_worker_dir() / name)]
    return [sys.executable, str(get_worker_dir() / "run_worker.py")]


def _stripped(name: str) -> bool:
    upper = name.upper()
    if upper in STRIPPED_NAMES or upper.startswith(STRIPPED_PREFIXES):
        return True
    return upper.endswith("_FILE") and upper not in KEPT_FILE_NAMES


def child_env(base=None, data_root: Optional[Path] = None) -> dict:
    """The worker command's environment (module doc).

    ``data_root``: the data folder this setup is saving, so the worker's log
    goes there; ignored when the environment already sets
    ``SETHLANS_DATA_ROOT``.
    """
    env = {name: value for name, value in (os.environ if base is None else base).items() if not _stripped(name)}
    if data_root is not None and not env.get(DATA_ROOT_ENV):
        env[DATA_ROOT_ENV] = str(data_root)
    return env


def _popen_flags() -> dict:
    # The manager may run without a console (the launcher starts it with
    # CREATE_NO_WINDOW); a console-mode child would then open a window.
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill ``proc`` and everything it started (a venv ``python.exe`` on
    Windows is a launcher with the real interpreter as its child)."""
    try:
        children = psutil.Process(proc.pid).children(recursive=True)
    except psutil.Error:
        children = []
    for child in children:
        try:
            child.kill()
        except psutil.Error:
            pass
    proc.kill()


@sensitive_variables("payload", "data")
def _execute(argv: List[str], data: bytes, timeout: float, env: dict):
    """``(exit code or None on timeout, stdout)``; ``OSError`` when it cannot start."""
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            env=env, **_popen_flags())
    try:
        out, _err = proc.communicate(data, timeout=timeout)
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=KILL_WAIT)
        except subprocess.TimeoutExpired:
            logger.error("The worker command (pid %d) did not end after it was killed", proc.pid)
        return None, b""


def parse_answer(out: bytes) -> Optional[dict]:
    """Exactly one JSON object (whitespace around it allowed), else ``None``."""
    if len(out) > MAX_ANSWER_BYTES:
        return None
    try:
        answer = json.loads(out.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return answer if isinstance(answer, dict) else None


def _result(command: str, code: Optional[int], out: bytes) -> CliResult:
    if code is None:
        return CliResult(command, error=TIMED_OUT, broken=True,
                         message="The worker program did not answer in time; see the worker log.")
    answer = parse_answer(out)
    if answer is None:
        return CliResult(command, error=BAD_ANSWER, broken=True,
                         message=f"The worker program gave no readable answer (exit code {code}); see the worker log.")
    if code == 0:
        return CliResult(command, answer=answer)
    error, message = answer.get("error"), answer.get("message")
    # The worker's message is shown on the setup page: printable text only, capped.
    text = clean_text(message, MAX_MESSAGE)
    if code == 1 and isinstance(error, str) and error and isinstance(message, str):
        return CliResult(command, answer=answer, error=error,
                         message=text or "The worker program refused the answers; see the worker log.")
    return CliResult(command, answer=answer, error=FAILED, broken=True,
                     message=text or "The worker program failed; see the worker log.")


@sensitive_variables("payload", "data")
def run(command: str, payload: dict, timeout: float, data_root: Optional[Path] = None) -> CliResult:
    """Run one setup command (module doc). Never raises for a worker failure."""
    if command not in COMMANDS:
        raise ValueError(f"unknown worker setup command {command!r}")
    argv = worker_command() + [command]
    data = json.dumps(payload).encode("utf-8")
    started = time.monotonic()
    try:
        code, out = _execute(argv, data, timeout, child_env(data_root=data_root))
    except OSError as exc:
        logger.error("Could not start the worker program %s: %s", argv, exc)
        return CliResult(command, error=UNAVAILABLE, broken=True,
                         message=f"Could not start the worker program ({argv[-2]}): {exc.strerror or exc}")
    elapsed = time.monotonic() - started
    if code is None:
        logger.error("Worker command %s timed out after %.0f s and was stopped", argv, elapsed)
    else:
        logger.info("Worker command %s exited %d after %.1f s", argv, code, elapsed)
    return _result(command, code, out)


def cards() -> CliResult:
    """``--setup-cards``: this machine's cards and the worker's saved choice."""
    return run(CARDS, {}, CARDS_TIMEOUT)


def discover(seconds: int = DISCOVER_SECONDS) -> CliResult:
    """``--setup-discover``: the managers announcing themselves for ``seconds``."""
    return run(DISCOVER, {"seconds": seconds}, seconds + DISCOVER_MARGIN)


def apply(payload: dict, data_root: Optional[Path] = None) -> CliResult:
    """``--setup-apply`` with the checked worker answers (they may hold the
    password and the key: stdin only, never logged)."""
    return run(APPLY, payload, APPLY_TIMEOUT, data_root=data_root)
