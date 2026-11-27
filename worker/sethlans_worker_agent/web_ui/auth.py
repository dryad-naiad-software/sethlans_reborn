# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The worker page password (setup-rebuild FR-WK-3, FR-WK-4, FR-SEC-8).

The password is stored only as a Django-format PBKDF2 hash
(``web_ui.password_hash``) under ``worker.ui_password_hash`` in
``config.json``. There is no default password: with none set,
``check_bearer`` answers ``NO_PASSWORD`` and the control actions are refused.

``check_bearer(environ)`` runs, in this order:

1. **Limit.** 10 failures per client address (``REMOTE_ADDR``, the real
   client because Waitress trusts Caddy's ``X-Forwarded-For``) in 60 s give
   ``LIMITED``; otherwise the attempt is counted as a failure now.
2. **Cache.** A hit (``auth_guard.ResultCache``) takes the counted attempt
   back and gives ``OK``.
3. **PBKDF2.** At most two run at once; a third gives ``BUSY`` (and takes the
   attempt back, as nothing was checked). A match takes the attempt back,
   stores a cache entry and gives ``OK``; else ``BAD``.
4. **Still current.** An ``OK`` from step 2 or 3 is given only when the
   stored hash, read again, is still the one checked; when the password was
   changed (or the file could not be read) meanwhile, the answer is ``BAD``.

The cache is cleared by ``set_password``; a hash written by another process
(``--setup-apply``) has a new salt, so entries made against the old hash
never hit again. The password is never logged.
"""
import logging
import threading

from sethlans_worker_agent import config_store
from sethlans_worker_agent.web_ui import password_hash
from sethlans_worker_agent.web_ui.auth_guard import FailureLimit, ResultCache

logger = logging.getLogger(__name__)

HASH_KEY = 'worker.ui_password_hash'
NO_PASSWORD_TEXT = "No worker page password is set; run Reset setup"

OK = 'ok'
BAD = 'bad'
NO_PASSWORD = 'no_password'
LIMITED = 'limited'
BUSY = 'busy'
UNREADABLE = 'unreadable'

MAX_PARALLEL_CHECKS = 2

_limit = FailureLimit()
_cache = ResultCache()
_checks = threading.BoundedSemaphore(MAX_PARALLEL_CHECKS)

_warned_lock = threading.Lock()
_warned_unusable = set()  # stored values already reported as unusable; under _warned_lock


def _warn_unusable_once(value) -> None:
    marker = repr(value)[:80]
    with _warned_lock:
        if marker in _warned_unusable:
            return
        _warned_unusable.add(marker)
    logger.error("The stored worker page password hash (%s) is not in the expected format; it is ignored "
                 "and no password counts as set. Run Reset setup to set one.", HASH_KEY)


def current_hash():
    """The stored hash when usable, ``None`` when none is set; ``config_store.ConfigReadError`` when unreadable."""
    section = config_store.load_strict().get('worker')
    value = section.get('ui_password_hash') if isinstance(section, dict) else None
    if value is None or value == '':
        return None
    if password_hash.parse(value) is None:
        _warn_unusable_once(value)
        return None
    return value


def is_password_configured() -> bool:
    """True when a usable password hash is stored (False also when the settings file cannot be read)."""
    try:
        return current_hash() is not None
    except config_store.ConfigReadError:
        return False


def set_password(password: str) -> None:
    """Store a new hash for ``password`` (the caller has checked the rules) and clear the cache."""
    config_store.set(HASH_KEY, password_hash.make(password))
    clear_cache()
    logger.info("Worker page password has been set.")


def clear_cache() -> None:
    _cache.clear()


def _bearer(environ: dict):
    header = environ.get('HTTP_AUTHORIZATION', '') or ''
    return header[len('Bearer '):] if header.startswith('Bearer ') else None


def _verify(client: str, stamp: float, stored: str, password: str) -> str:
    """Step 3: PBKDF2 under the semaphore."""
    if not _checks.acquire(blocking=False):
        _limit.forgive(client, stamp)
        return BUSY
    try:
        matched = password_hash.verify(password, stored)
    finally:
        _checks.release()
    if not matched:
        return BAD
    _limit.forgive(client, stamp)
    _cache.store(stored, password)
    return OK


def check_bearer(environ: dict) -> str:
    """Check ``Authorization: Bearer <password>``; one of ``OK``, ``BAD``, ``NO_PASSWORD``, ``LIMITED``, ``BUSY``,
    ``UNREADABLE``."""
    try:
        stored = current_hash()
    except config_store.ConfigReadError:
        logger.warning("The worker settings file could not be read for a password check.")
        return UNREADABLE
    if stored is None:
        return NO_PASSWORD
    client = environ.get('REMOTE_ADDR') or 'unknown'
    stamp = _limit.begin(client)
    if stamp is None:
        return LIMITED
    password = _bearer(environ)
    if not password:
        return BAD
    if _cache.hit(stored, password):
        _limit.forgive(client, stamp)
        return _still_current(stored)
    result = _verify(client, stamp, stored, password)
    return _still_current(stored) if result == OK else result


def _still_current(checked: str) -> str:
    """Step 4: ``OK`` when the stored hash is still ``checked``, else ``BAD`` (changed or unreadable meanwhile)."""
    try:
        if current_hash() == checked:
            return OK
    except config_store.ConfigReadError:
        logger.warning("The worker settings file could not be read again after a password check.")
        return BAD
    logger.info("The worker page password changed during a password check; the request is refused.")
    return BAD


def reset_for_tests() -> None:
    """Forget the counted failures, the cache and the warnings. Test-only hook."""
    _limit.clear()
    _cache.clear()
    with _warned_lock:
        _warned_unusable.clear()
