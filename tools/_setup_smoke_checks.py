# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The checks of ``tools/setup_smoke.py`` that need no running process.

Each ``*_problems`` function returns a list of problems (empty when all
is well), so the smoke can print every one before it fails. Stdlib only.
"""

from __future__ import annotations

import configparser
import datetime
import json
import pathlib
import tempfile
from typing import Mapping, Optional

ADMIN_USERNAME = "smokeadmin"
ADMIN_EMAIL = "smoke@example.com"
PBKDF2_PREFIX = "pbkdf2_sha256$"
COMPLETED_AT_MAX_AGE_SECONDS = 600

# ``run_worker --setup-cards`` answer (worker setup_cli/cards_cmd.py doc).
_BOOL = (bool,)
_OPT_BOOL = (bool, type(None))
_OPT_INT = (int, type(None))
_OPT_STR = (str, type(None))
CARDS_TOP_KEYS = {
    "os_probe_failed": _BOOL,
    "cards": (list,),
    "saved": (dict,),
    "unreal_enabled": _BOOL,
    "unreal_enabled_env": _BOOL,
    "enrollment": (dict, type(None)),
    "ui_password_set": _BOOL,
}
CARD_KEYS = {
    "address": (str,), "key": (str,), "pci": _OPT_STR, "name": (str,),
    "brand": _OPT_STR, "vendor_id": _OPT_INT, "device_id": _OPT_INT,
    "revision_id": _OPT_INT, "screen": _OPT_BOOL, "primary_screen": _OPT_BOOL,
}
SAVED_KEYS = {
    "compute": (str,), "cards": (list,), "combine": _BOOL,
    "env_override": _BOOL, "config_read_failed": _BOOL,
}
ENROLLMENT_KEYS = {
    "enrolled": _BOOL, "manager_id": _OPT_STR, "manager_name": _OPT_STR,
    "host": _OPT_STR, "port": _OPT_INT,
}


def smoke_parent(environ: Mapping[str, str], platform: str) -> pathlib.Path:
    """Where the smoke makes its scratch folder.

    ``$RUNNER_TEMP`` in GitHub Actions, else the system temp folder. On
    macOS the system temp folder is ``$TMPDIR``, under ``/private/var``,
    which setup refuses as a data folder, so ``/tmp`` is used there.
    """
    runner_temp = environ.get("RUNNER_TEMP", "").strip()
    if runner_temp:
        return pathlib.Path(runner_temp)
    if platform == "darwin":
        return pathlib.Path("/tmp")
    return pathlib.Path(tempfile.gettempdir())


def manager_answers(data_root: pathlib.Path, port: int, password: str) -> dict:
    """A valid ``manager``-mode submit: loopback bind, SQLite."""
    return {
        "mode": "manager",
        "server": {"host": "127.0.0.1", "port": port},
        "data_folder": {"path": str(data_root)},
        "database": {"engine": "sqlite"},
        "admin": {"username": ADMIN_USERNAME, "email": ADMIN_EMAIL,
                  "password": password},
    }


def health_problems(status: int, payload: object) -> list[str]:
    """``GET /api/health/`` in setup mode: 200, ``mode: setup`` and a ``boot_id``."""
    if status != 200:
        return [f"health answered {status}, not 200"]
    if not isinstance(payload, dict):
        return ["health did not answer a JSON object"]
    problems = []
    if payload.get("mode") != "setup":
        problems.append(f"health mode is {payload.get('mode')!r}, not 'setup'")
    boot_id = payload.get("boot_id")
    if not isinstance(boot_id, str) or not boot_id:
        problems.append(f"health has no boot_id (got {boot_id!r})")
    return problems


def page_problems(status: int, headers: Mapping[str, str], body: bytes) -> list[str]:
    """``GET /setup/``: 200 with an HTML page."""
    problems = []
    if status != 200:
        problems.append(f"/setup/ answered {status}, not 200")
    content_type = headers.get("content-type", "")
    if not content_type.startswith("text/html"):
        problems.append(f"/setup/ content type is {content_type!r}, not text/html")
    if b"<html" not in body[:4096].lower():
        problems.append("/setup/ body is not an HTML page")
    return problems


def _completed_at_problems(raw: str, now: datetime.datetime) -> list[str]:
    try:
        completed = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return [f"[setup] completed_at {raw!r} is not an ISO 8601 time"]
    if completed.tzinfo is None:
        return [f"[setup] completed_at {raw!r} has no time zone"]
    age = (now - completed).total_seconds()
    if not -60 <= age <= COMPLETED_AT_MAX_AGE_SECONDS:
        return [f"[setup] completed_at {raw!r} is not from this run ({age:.0f} s ago)"]
    return []


def ini_problems(text: str, now: Optional[datetime.datetime] = None) -> list[str]:
    """``sethlans.ini`` after a manager-mode submit (setup-rebuild FR-SM-4)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    parser = configparser.RawConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        return [f"sethlans.ini does not parse: {exc}"]
    get = parser.get
    problems = []
    if get("setup", "version", fallback=None) != "1":
        problems.append(f"[setup] version is {get('setup', 'version', fallback=None)!r}, not '1'")
    if get("setup", "mode", fallback=None) != "manager":
        problems.append(f"[setup] mode is {get('setup', 'mode', fallback=None)!r}, not 'manager'")
    completed = get("setup", "completed_at", fallback=None)
    if completed is None:
        problems.append("[setup] has no completed_at")
    else:
        problems.extend(_completed_at_problems(completed, now))
    password_hash = get("first_admin", "password_hash", fallback="")
    if not password_hash.startswith(PBKDF2_PREFIX):
        problems.append(f"[first_admin] password_hash does not start with {PBKDF2_PREFIX!r}")
    return problems


def files_holding(root: pathlib.Path, secret: bytes) -> list[pathlib.Path]:
    """Every file under ``root`` whose bytes contain ``secret``."""
    hits = []
    for path in sorted(root.rglob("*")):
        try:
            if path.is_file() and secret in path.read_bytes():
                hits.append(path)
        except OSError:
            continue
    return hits


def _typed_problems(where: str, value: object, spec: Mapping[str, tuple]) -> list[str]:
    if not isinstance(value, dict):
        return [f"{where} is not a JSON object"]
    problems = []
    if set(value) != set(spec):
        problems.append(f"{where} keys are {sorted(value)}, expected {sorted(spec)}")
    for key, types in spec.items():
        if key in value and not _is(value[key], types):
            problems.append(f"{where}.{key} is {type(value[key]).__name__}, expected "
                            + " or ".join(t.__name__ for t in types))
    return problems


def _is(value: object, types: tuple) -> bool:
    # JSON true/false load as bool, a subclass of int: no bool where an int is meant.
    if isinstance(value, bool) and bool not in types:
        return False
    return isinstance(value, types)


def cards_problems(stdout: bytes) -> list[str]:
    """``run_worker --setup-cards`` stdout: exactly one JSON line in the documented shape.

    The number of cards is not checked (a build runner may have none).
    """
    lines = stdout.decode("utf-8", errors="replace").splitlines()
    if len(lines) != 1:
        return [f"--setup-cards printed {len(lines)} lines, expected exactly 1"]
    try:
        answer = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        return [f"--setup-cards output is not JSON: {exc}"]
    problems = _typed_problems("answer", answer, CARDS_TOP_KEYS)
    if not isinstance(answer, dict):
        return problems
    cards = answer.get("cards")
    for index, card in enumerate(cards if isinstance(cards, list) else []):
        problems.extend(_typed_problems(f"cards[{index}]", card, CARD_KEYS))
    saved = answer.get("saved")
    if isinstance(saved, dict):
        problems.extend(_typed_problems("saved", saved, SAVED_KEYS))
        ticked = saved.get("cards")
        for index, address in enumerate(ticked if isinstance(ticked, list) else []):
            if not isinstance(address, str):
                problems.append(f"saved.cards[{index}] is not text")
    if isinstance(answer.get("enrollment"), dict):
        problems.extend(_typed_problems("enrollment", answer["enrollment"], ENROLLMENT_KEYS))
    return problems
