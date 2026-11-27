# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Worker, Manager and Enrollment key sections of the setup answers
(FR-WZ-3, FR-WZ-4, FR-WZ-5, FR-WZ-6, FR-RS-3).

Worker (``manager_worker`` and ``worker``), checked against a fresh
``--setup-cards`` answer (``worker-card-selection.md`` O1):
``worker.compute`` (``cpu``, ``gpu``, ``cpu_gpu``), ``worker.cards`` (card
addresses from that list, no card twice, one brand), GPU rendering with no
card refused, ``worker.combine`` only with two or more cards,
``worker.unreal_enabled``, and ``worker.ui_password``: Django's validators
for an unsaved ``User`` named after this computer, plus the worker's own
rule that a browser can send it (printable Latin-1, no space at either
end). An empty password keeps the stored one when the worker has one.
While ``SETHLANS_COMPUTE_*`` set the card choice the worker does not save
it, so only its shape is checked.

Manager and Enrollment key (``worker`` only): ``manager.url`` as
``https://host[:port]`` (port 8080 when none is given) and
``manager.enrollment_key`` passing the manager's ``enrollment_key.normalize``;
or ``manager.keep_enrollment`` when ``--setup-cards`` reports an
enrollment. ``manager.name`` (the chosen discovered manager's name, as
printable text) goes to ``--setup-apply`` as ``manager_name``; the worker
saves it with the enrollment and the finish page shows it.

The result is the ``--setup-apply`` payload; it may hold the password and
the key, so it is never logged.
"""
import urllib.parse
from typing import List, Optional

from django.views.decorators.debug import sensitive_variables

from sethlans_manager.setup_mode import fields, net, worker_cli
from sethlans_manager.setup_mode.validate_admin import check_password
from sethlans_manager.setup_mode.worker_form import MAX_CARDS, METHODS, card_list, clean_text, enrollment_of, saved_worker

WORKER_MODES = ("manager_worker", "worker")
DEFAULT_MANAGER_PORT = 8080
URL_PATHS = ("", "/", "/api", "/api/")
URL_SHAPE = "Enter the manager as https://host or https://host:port, with nothing after it."
NO_CARD = "GPU rendering needs at least one ticked graphics card."
NOT_ENROLLED = "This worker is not enrolled with a manager yet; choose a manager and enter its enrollment key."
EDGE_SPACE = "This password cannot start or end with a space."
NOT_SENDABLE = ("This password has a character a browser cannot send: use letters, digits, punctuation and "
                "spaces from Latin-1 only.")


class WorkerCliFailure(Exception):
    """A worker setup command failed while the answers were checked."""

    def __init__(self, result: worker_cli.CliResult):
        super().__init__(result.message)
        self.result = result


def fresh_cards() -> dict:
    """A fresh ``--setup-cards`` answer; :class:`WorkerCliFailure` when it fails."""
    result = worker_cli.cards()
    if not result.ok:
        raise WorkerCliFailure(result)
    return result.answer


def _method(worker: dict, errors: fields.Errors) -> Optional[str]:
    method = fields.text(worker, "compute", "worker.compute", errors, required=True, label="The compute method")
    if method is not None and method not in METHODS:
        errors.add("worker.compute", "Choose cpu, gpu or cpu_gpu.")
        return None
    return method


def _ticked(worker: dict, errors: fields.Errors) -> Optional[List[str]]:
    value = worker.get("cards", [])
    if value is None:
        value = []
    if not isinstance(value, list) or len(value) > MAX_CARDS or not all(isinstance(item, str) for item in value):
        errors.add("worker.cards", f"Send the ticked cards as a list of at most {MAX_CARDS} card addresses.")
        return None
    if len(set(value)) != len(value):
        errors.add("worker.cards", "A graphics card is ticked more than once.")
        return None
    return value


def _check_cards(method: Optional[str], ticked: List[str], combine: bool, found: list, errors: fields.Errors):
    by_address = {card["address"]: card for card in found}
    unknown = [address for address in ticked if address not in by_address]
    if unknown:
        errors.add("worker.cards", f"Not a graphics card of this machine: {', '.join(unknown)}.")
        return
    if method in ("gpu", "cpu_gpu") and not ticked:
        errors.add("worker.cards", NO_CARD)
    brands = {by_address[address]["brand"] for address in ticked} - {None}
    if len(brands) > 1:
        errors.add("worker.cards", f"Cards of more than one brand cannot be ticked together ({', '.join(sorted(brands))}).")
    if combine and len(ticked) < 2:
        errors.add("worker.combine", "Combine GPUs needs two or more ticked graphics cards.")


@sensitive_variables("worker", "password")
def _ui_password(worker: dict, stored_set: bool, errors: fields.Errors) -> Optional[str]:
    """The new password; ``None`` keeps the stored one (or after an error)."""
    password = worker.get("ui_password")
    if password in (None, "") and stored_set:
        return None
    if not check_password(password, net.hostname(), "", "worker.ui_password", errors):
        return None
    if password != password.strip(" "):
        errors.add("worker.ui_password", EDGE_SPACE)
    if not all(" " <= ch <= "~" or "\xa0" <= ch <= "\xff" for ch in password):
        errors.add("worker.ui_password", NOT_SENDABLE)
    return password


@sensitive_variables("answers", "worker", "password")
def worker_section(answers: dict, cards_answer: dict, errors: fields.Errors) -> Optional[dict]:
    """The ``--setup-apply`` fields of the Worker section, or ``None`` after an error."""
    worker = fields.section(answers, "worker", errors)
    saved = saved_worker(cards_answer)
    method = _method(worker, errors)
    ticked = _ticked(worker, errors)
    combine = fields.boolean(worker, "combine", "worker.combine", errors, default=False)
    unreal = fields.boolean(worker, "unreal_enabled", "worker.unreal_enabled", errors, default=False)
    if ticked is not None and not saved["compute_env_override"]:
        _check_cards(method, ticked, combine, card_list(cards_answer), errors)
    password = _ui_password(worker, saved["ui_password_set"], errors)
    if errors.has("worker"):
        return None
    return {"compute": method, "cards": ticked, "combine": combine, "unreal_enabled": unreal,
            "ui_password": password}


def manager_url(text: str) -> str:
    """``https://host:port`` from ``https://host[:port]``; ``ValueError(URL_SHAPE)`` otherwise.

    The same rules as the worker's ``setup_cli.enroll.parse_manager_url``.
    """
    parts = urllib.parse.urlsplit(text)
    try:
        port = parts.port
    except ValueError as invalid:
        raise ValueError(URL_SHAPE) from invalid
    if parts.scheme != "https" or not parts.hostname or port == 0:
        raise ValueError(URL_SHAPE)
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in URL_PATHS:
        raise ValueError(URL_SHAPE)
    host = parts.hostname
    if ":" in host:
        host = f"[{host}]"
    return f"https://{host}:{port or DEFAULT_MANAGER_PORT}"


@sensitive_variables("raw", "manager")
def _enrollment_key(manager: dict, errors: fields.Errors) -> Optional[str]:
    from workers.enrollment_key import normalize  # stdlib only; the app itself is never loaded

    raw = fields.text(manager, "enrollment_key", "manager.enrollment_key", errors, required=True, max_length=64,
                      label="The enrollment key")
    if raw is None:
        return None
    try:
        return normalize(raw)
    except ValueError:
        errors.add("manager.enrollment_key", "Enter the enrollment key shown on the manager (16 letters and digits).")
        return None


@sensitive_variables("answers", "manager", "key")
def manager_section(answers: dict, cards_answer: dict, errors: fields.Errors) -> Optional[dict]:
    """The enrollment fields of ``--setup-apply`` (``{}`` keeps the enrollment), or ``None`` after an error."""
    manager = fields.section(answers, "manager", errors)
    if fields.boolean(manager, "keep_enrollment", "manager.keep_enrollment", errors, default=False):
        if enrollment_of(cards_answer) is None:
            errors.add("manager.keep_enrollment", NOT_ENROLLED)
            return None
        return {}
    url = fields.text(manager, "url", "manager.url", errors, required=True, max_length=2048, label="The manager URL")
    if url is not None:
        try:
            url = manager_url(url)
        except ValueError as exc:
            errors.add("manager.url", str(exc))
            url = None
    key = _enrollment_key(manager, errors)
    name = clean_text(manager.get("name"))
    if url is None or key is None:
        return None
    return {"manager_url": url, "enrollment_key": key, "manager_name": name or None}
