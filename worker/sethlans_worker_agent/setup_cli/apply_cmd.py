# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``--setup-apply``: save the worker part of the setup form (setup-rebuild FR-WK-1, FR-WK-3, FR-WK-5).

Input::

    {"compute": "cpu" | "gpu" | "cpu_gpu", "cards": [<address>, ...],
     "combine": bool, "unreal_enabled": bool,
     "ui_password": <text> | null,
     "manager_url": "https://host[:port]", "enrollment_key": <text>,   (both or neither)
     "manager_name": <text>}                                           (optional)

``ui_password`` null (or left out) keeps the stored password; with none
stored that is refused. ``manager_name`` is echoed back and, when the
worker enrolls, saved as ``manager.name`` (``null`` when left out), for
``--setup-cards``'s ``enrollment``; without enrollment the stored name is
left alone. Order: every field is checked (``apply_fields``),
then, when a key is given, the worker enrolls (``enroll.enroll_pairs``),
then everything goes into ``config.json`` with one ``config_store.set_many``:
the card choice, ``unreal.enabled``, ``worker.ui_password_hash`` and the
enrollment. A failure at any step writes nothing. That write is a
compare-and-set on ``compute.revision``: when the running agent's page saved
a card choice after this command read it, nothing is written and the answer
is ``card_changed``. The card choice is skipped
(one INFO line) while ``SETHLANS_COMPUTE_*`` set it, ``unreal.enabled``
while ``SETHLANS_UNREAL_ENABLED`` does.

Output::

    {"enrolled": bool, "manager_id", "manager_name", "cert_fingerprint", "status"}

``status`` is the enroll answer's ``status`` when the manager sent one
(worker approval, decision C), else ``null``; without enrollment all but
``enrolled`` are ``null``. The key and the password are never logged or
put in an answer.
"""
import logging
import socket

from sethlans_worker_agent import config_compute, config_store, enrollment_client, unreal_switch
from sethlans_worker_agent.setup_cli import apply_fields, cards_cmd, enroll
from sethlans_worker_agent.setup_cli.stdio import CliError, check_fields
from sethlans_worker_agent.web_ui import auth, password_hash

logger = logging.getLogger(__name__)

FIELDS = ('compute', 'cards', 'combine', 'unreal_enabled', 'ui_password',
          'manager_url', 'enrollment_key', 'manager_name')


def _manager(request: dict):
    """``(host, port, key)`` when enrollment was asked for, else ``None``."""
    url, key = request.get('manager_url'), request.get('enrollment_key')
    if url is None and key is None:
        return None
    if url is None or key is None:
        raise CliError('bad_input', "manager_url and enrollment_key go together: give both or neither.")
    if not isinstance(key, str) or not key.strip():
        raise CliError('bad_input', "enrollment_key must be text.")
    try:
        host, port = enroll.parse_manager_url(url)
    except ValueError as invalid:
        raise CliError('bad_input', str(invalid))
    return host, port, key


def _password_pairs(request: dict, hostname: str) -> list:
    new_password = apply_fields.ui_password(request, hostname)
    if new_password is not None:
        return [(auth.HASH_KEY, password_hash.make(new_password))]
    try:
        stored = auth.current_hash()
    except config_store.ConfigReadError as error:
        raise CliError('config_unreadable', f"The worker's settings file could not be read: {error}")
    if stored is None:
        raise CliError('bad_input', "ui_password is needed: no worker page password is stored yet.")
    return []


def _choice_pairs(request: dict):
    """``(pairs, expect)``: ``expect`` checks the revision the card choice was based on (``None``: no choice saved)."""
    chosen_method = apply_fields.method(request)
    ticked = apply_fields.card_keys(request)
    wants_combine = apply_fields.combine(request)
    wants_unreal = apply_fields.unreal_enabled(request)
    section = apply_fields.compute_section(strict=True)
    pairs, expect = [], None
    if section.env_override:
        logger.info("Setup: the graphics card choice is not saved: SETHLANS_COMPUTE_* variables set it.")
    else:
        pairs += apply_fields.card_pairs(section, chosen_method, wants_combine, ticked, cards_cmd.probe())
        expect = {'compute.revision': config_compute.revision_is(section.revision)}
    if unreal_switch.env_set():
        logger.info("Setup: the Unreal switch is not saved: %s sets it.", unreal_switch.ENV_VAR)
    else:
        pairs.append((unreal_switch.KEY, wants_unreal))
    return pairs, expect


def _enroll(manager, hostname: str, name):
    host, port, key = manager
    try:
        return enroll.enroll_pairs(host, port, key, hostname, name)
    except enrollment_client.EnrollmentError as error:
        code = enroll.error_code(error)
        logger.warning("Setup: enrollment with the manager at %s:%d failed (%s).", host, port, code)
        raise CliError(code, str(error))


def _save(pairs: list, expect) -> None:
    try:
        config_store.set_many(pairs, expect=expect)
    except config_store.ConfigChangedError:
        logger.warning("Setup: nothing saved: the graphics card choice was changed on the worker page meanwhile.")
        raise CliError('card_changed', "The graphics card choice was changed on the worker page while setup ran; "
                                       "nothing was saved. Read the cards again and apply once more.")
    except (config_store.ConfigLockTimeoutError, OSError) as error:
        logger.error("Setup: the worker settings could not be saved: %s", error)
        raise CliError('save_failed', f"The worker's settings could not be saved: {error}")


def run(request: dict) -> dict:
    check_fields(request, FIELDS)
    hostname = socket.gethostname()
    manager = _manager(request)
    name = apply_fields.manager_name(request)
    pairs, expect = _choice_pairs(request)
    pairs += _password_pairs(request, hostname)
    answer = {'enrolled': False, 'manager_id': None, 'manager_name': None, 'cert_fingerprint': None, 'status': None}
    if manager is not None:
        result, enrollment = _enroll(manager, hostname, name)
        pairs += enrollment
        answer.update(enrolled=True, manager_id=result['manager_id'], manager_name=name,
                      cert_fingerprint=result['cert_fingerprint'], status=result.get('status'))
    _save(pairs, expect)
    auth.clear_cache()
    logger.info("Setup: worker settings saved (%s).", ', '.join(dotted for dotted, _value in pairs))
    if manager is not None:
        logger.info("Setup: enrolled with manager %s at %s:%d.", answer['manager_id'], manager[0], manager[1])
    return answer
