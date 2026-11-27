# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The worker parts of ``GET /api/setup/form/`` (FR-WZ-1, FR-WZ-4, FR-RS-3, FR-SEC-9).

``--setup-cards`` and ``--setup-discover`` run side by side on two daemon
threads, each waited for at most its command's timeout plus the kill wait
(a thread still running then counts as a timeout and is left behind; setup
never waits for it to exit). They take about as long as the discovery
(5 s); the form's database check runs before them (``form``). Their
answers are copied field by field with type checks, never passed through:

* ``cards``: this machine's graphics cards (``worker-card-selection.md`` O1);
* ``worker``: the worker's saved choice, or the defaults (method ``cpu``,
  no card ticked), an empty ``ui_password`` and ``ui_password_set``;
* ``managers``: at most 32 announcements from the network, with control
  characters taken out of the text and a ready ``url``;
* ``enrollment``: the manager this worker is enrolled with,
  ``{"manager_id", "manager_name", "host", "port"}`` (each may be
  ``null``; the worker never sends the token or the fingerprint), or
  ``null`` when it is not enrolled.

A failed command (or any exception while running it) adds one
``{"part": "worker", "message"}`` entry to the form's ``errors`` and leaves
its parts at their defaults; the form is still returned.
"""
import ipaddress
import logging
import threading
import time
from typing import Callable, Optional

from sethlans_manager.setup_mode import worker_cli
from sethlans_manager.setup_mode.clean import clean_text

logger = logging.getLogger(__name__)

METHODS = ("cpu", "gpu", "cpu_gpu")
BRANDS = ("NVIDIA", "AMD", "Intel", "Apple")
MAX_MANAGERS = 32
MAX_CARDS = 16
MAX_HOST = 253
MAX_CARD_NAME = 128
PART = "worker"
CARDS_WAIT = worker_cli.CARDS_TIMEOUT + worker_cli.KILL_WAIT
DISCOVER_WAIT = worker_cli.DISCOVER_SECONDS + worker_cli.DISCOVER_MARGIN + worker_cli.KILL_WAIT


def _bool(value) -> bool:
    return value is True


def _int_or_none(value) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _id_text(value) -> Optional[str]:
    """A PCI id as the worker gives it (text or a number), else ``None``."""
    if isinstance(value, str):
        return clean_text(value, 16) or None
    return _int_or_none(value)


def _card(raw) -> Optional[dict]:
    if not isinstance(raw, dict) or not isinstance(raw.get("address"), str) or not raw["address"]:
        return None
    brand = raw.get("brand")
    return {
        "address": clean_text(raw["address"], 32),
        "key": clean_text(raw.get("key"), 40),
        "pci": clean_text(raw.get("pci"), 16) or None,
        "name": clean_text(raw.get("name"), MAX_CARD_NAME),
        "brand": brand if brand in BRANDS else None,
        "vendor_id": _id_text(raw.get("vendor_id")),
        "device_id": _id_text(raw.get("device_id")),
        "revision_id": _id_text(raw.get("revision_id")),
        "screen": _bool(raw.get("screen")),
        "primary_screen": _bool(raw.get("primary_screen")),
    }


def card_list(answer: dict) -> list:
    raw = answer.get("cards")
    found = [_card(item) for item in raw[:MAX_CARDS]] if isinstance(raw, list) else []
    return [card for card in found if card is not None]


def _enrollment(raw) -> Optional[dict]:
    """``--setup-cards``' ``enrollment`` object (module doc), else ``None``."""
    if not isinstance(raw, dict) or raw.get("enrolled") is not True:
        return None
    port = _int_or_none(raw.get("port"))
    return {
        "manager_id": clean_text(raw.get("manager_id")) or None,
        "manager_name": clean_text(raw.get("manager_name")) or None,
        "host": clean_text(raw.get("host"), MAX_HOST) or None,
        "port": port if port is not None and 0 < port < 65536 else None,
    }


def saved_worker(answer: dict) -> dict:
    """The worker section's earlier values from a ``--setup-cards`` answer (no secret)."""
    saved = answer.get("saved") if isinstance(answer.get("saved"), dict) else {}
    method = saved.get("compute")
    cards = saved.get("cards") if isinstance(saved.get("cards"), list) else []
    return {
        "compute": method if method in METHODS else "cpu",
        "cards": [clean_text(item, 32) for item in cards if isinstance(item, str)][:MAX_CARDS],
        "combine": _bool(saved.get("combine")),
        "unreal_enabled": _bool(answer.get("unreal_enabled")),
        "ui_password": "",
        "ui_password_set": _bool(answer.get("ui_password_set")),
        "compute_env_override": _bool(saved.get("env_override")),
        "unreal_enabled_env": _bool(answer.get("unreal_enabled_env")),
    }


def enrollment_of(answer: dict) -> Optional[dict]:
    return _enrollment(answer.get("enrollment"))


def _url_host(ip: str, host: str) -> str:
    try:
        return str(ipaddress.IPv4Address(ip))
    except ValueError:
        return host


def _manager(raw) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    manager_id = clean_text(raw.get("manager_id"))
    port = _int_or_none(raw.get("port"))
    if not manager_id or port is None or not 0 < port < 65536:
        return None
    host = clean_text(raw.get("host"), MAX_HOST)
    ip = clean_text(raw.get("ip"), 15)
    target = _url_host(ip, host if " " not in host else "")
    return {
        "name": clean_text(raw.get("name")),
        "host": host,
        "ip": ip,
        "port": port,
        "manager_id": manager_id,
        "version": clean_text(raw.get("version"), 32),
        "url": f"https://{target}:{port}" if target else None,
    }


def manager_list(answer: dict) -> list:
    raw = answer.get("managers")
    found = [_manager(item) for item in raw[:MAX_MANAGERS]] if isinstance(raw, list) else []
    return [entry for entry in found if entry is not None]


def _failure(what: str, result: worker_cli.CliResult) -> dict:
    return {"part": PART, "message": f"Could not {what}: {result.message}"}


class _Call:
    """One worker command on a daemon thread; :meth:`result` never raises."""

    def __init__(self, command: str, call: Callable[[], worker_cli.CliResult], wait: float):
        self.command, self.call, self.wait = command, call, wait
        self.outcome: Optional[worker_cli.CliResult] = None
        self.thread = threading.Thread(target=self._run, name=f"setup-worker-cli{command}", daemon=True)

    def _run(self) -> None:
        try:
            self.outcome = self.call()
        except Exception:  # noqa: BLE001 - any failure is a broken result, never a 500
            logger.exception("The worker command %s failed in the manager", self.command)
            self.outcome = worker_cli.CliResult(self.command, error=worker_cli.FAILED, broken=True,
                                                message="The worker program could not be run; see the setup log.")

    def result(self, started: float) -> worker_cli.CliResult:
        self.thread.join(max(0.0, started + self.wait - time.monotonic()))
        if self.outcome is not None:
            return self.outcome
        logger.error("The worker command %s did not finish within %.0f s; not waiting for it", self.command, self.wait)
        return worker_cli.CliResult(self.command, error=worker_cli.TIMED_OUT, broken=True,
                                    message="The worker program did not answer in time; see the worker log.")


def _run_both():
    calls = (_Call(worker_cli.CARDS, worker_cli.cards, CARDS_WAIT),
             _Call(worker_cli.DISCOVER, worker_cli.discover, DISCOVER_WAIT))
    started = time.monotonic()
    for call in calls:
        call.thread.start()
    return tuple(call.result(started) for call in calls)


def form_parts(errors: list) -> dict:
    """``cards``, ``cards_os_probe_failed``, ``worker``, ``managers`` and
    ``enrollment`` for the form; problems are appended to ``errors`` (module doc)."""
    cards_result, discover_result = _run_both()
    cards_answer = cards_result.answer if cards_result.ok else {}
    if not cards_result.ok:
        errors.append(_failure("list this machine's graphics cards", cards_result))
    elif cards_answer.get("os_probe_failed") is True:
        errors.append({"part": PART, "message": "The operating system's list of graphics cards could not be read."})
    if not discover_result.ok:
        errors.append(_failure("look for managers on the network", discover_result))
    return {
        "cards": card_list(cards_answer),
        "cards_os_probe_failed": _bool(cards_answer.get("os_probe_failed")),
        "worker": saved_worker(cards_answer),
        "managers": manager_list(discover_result.answer) if discover_result.ok else [],
        "enrollment": enrollment_of(cards_answer),
    }
