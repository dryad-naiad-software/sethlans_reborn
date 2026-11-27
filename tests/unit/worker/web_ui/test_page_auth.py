# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The worker page password check (``auth.check_bearer``, ``auth_guard``; setup-rebuild FR-SEC-8, FR-WK-4).

``check_bearer`` runs its real path against a real ``config.json`` in an
isolated worker folder, holding ``test-token`` hashed with few rounds
(``_page_password.fast_hash``). ``password_hash.verify`` is wrapped to count
the PBKDF2 checks, or replaced by one held on an Event to run checks in
parallel. ``auth_guard`` is tested on its own with an injected clock.
"""
import threading

import pytest

from sethlans_worker_agent import config_store
from sethlans_worker_agent.web_ui import auth, password_hash
from sethlans_worker_agent.web_ui.auth_guard import FailureLimit, ResultCache
from tests.unit.worker._page_password import fast_hash, write_hash

WAIT = 5.0  # upper bound for every Event wait and join; never reached when the code is right
LAN_A = '192.168.1.20'
LAN_B = '192.168.1.21'


def _check(password, client=LAN_A):
    return auth.check_bearer({'HTTP_AUTHORIZATION': f'Bearer {password}', 'REMOTE_ADDR': client})


@pytest.fixture
def pbkdf2_calls(monkeypatch):
    """Counts the PBKDF2 checks; each still runs the real ``verify``."""
    calls = []
    real = password_hash.verify

    def counted(password, encoded):
        calls.append(password)
        return real(password, encoded)
    monkeypatch.setattr(password_hash, 'verify', counted)
    return calls


@pytest.fixture
def held_checks(monkeypatch):
    """``verify`` waits for ``release``; ``entered`` counts the checks inside it."""
    state = type('Held', (), {})()
    state.release = threading.Event()
    state.inside = threading.Semaphore(0)

    def held(_password, _encoded):
        state.inside.release()
        assert state.release.wait(WAIT)
        return False
    monkeypatch.setattr(password_hash, 'verify', held)
    return state


def _in_thread(target):
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


class TestLimit:

    def test_ten_failures_from_one_address_give_429_while_another_is_fine(self, page_password):
        """FR-SEC-8, FR-SEC-3: 10 failures per client address in 60 s, then ``LIMITED`` even with the right password."""
        assert [_check('wrong') for _ in range(10)] == [auth.BAD] * 10
        assert _check(page_password) == auth.LIMITED
        assert _check(page_password, LAN_B) == auth.OK

    def test_the_attempt_is_counted_before_hashing(self, page_password, held_checks):
        """FR-SEC-8 step 1: while the 10th guess is still hashing, an 11th from that address is refused at once."""
        for _ in range(9):
            auth._limit.begin(LAN_A)
        tenth = _in_thread(lambda: _check('wrong'))
        assert held_checks.inside.acquire(timeout=WAIT)
        try:
            assert _check('another-guess') == auth.LIMITED
        finally:
            held_checks.release.set()
            tenth.join(WAIT)
        assert not tenth.is_alive()

    def test_window_is_sixty_seconds(self):
        """FR-SEC-8: the failures count in a sliding 60 s window (injected clock)."""
        now = [0.0]
        limit = FailureLimit(clock=lambda: now[0])
        stamps = [limit.begin(LAN_A) for _ in range(10)]
        assert None not in stamps and limit.begin(LAN_A) is None
        now[0] = 59.9
        assert limit.begin(LAN_A) is None
        now[0] = 60.0
        assert limit.begin(LAN_A) == 60.0

    def test_a_right_password_takes_its_attempt_back(self, page_password):
        """FR-SEC-8 steps 2-3: only failures count, so many right answers never lock the address."""
        assert [_check(page_password) for _ in range(12)] == [auth.OK] * 12


class TestCache:

    def test_repeat_skips_pbkdf2_and_a_changed_hash_misses(self, page_password, pbkdf2_calls, worker_home):
        """FR-SEC-8 step 2: a repeat is a cache hit; after the stored hash changes it is checked again."""
        assert _check(page_password) == auth.OK and _check(page_password) == auth.OK
        assert len(pbkdf2_calls) == 1
        write_hash(worker_home, fast_hash(page_password, salt='anothersalt0123456789abcdef01234'))
        assert _check(page_password) == auth.OK
        assert len(pbkdf2_calls) == 2

    def test_a_wrong_password_is_never_a_hit(self, page_password, pbkdf2_calls):
        """FR-SEC-8 step 2: only a correct password is cached."""
        assert _check(page_password) == auth.OK
        assert _check('wrong') == auth.BAD and _check('wrong') == auth.BAD
        assert len(pbkdf2_calls) == 3

    def test_entry_expires_ten_minutes_after_creation(self):
        """FR-SEC-8 step 2: ``expires`` = creation + 10 minutes, fixed (a hit or a new store does not extend it)."""
        now = [0.0]
        cache = ResultCache(clock=lambda: now[0])
        cache.store('hash-1', 'pw')
        now[0] = 300.0
        assert cache.hit('hash-1', 'pw') is True
        cache.store('hash-1', 'pw')
        now[0] = 599.9
        assert cache.hit('hash-1', 'pw') is True and cache.hit('hash-1', 'other') is False
        now[0] = 600.0
        assert cache.hit('hash-1', 'pw') is False

    def test_set_password_clears_the_cache(self, page_password, pbkdf2_calls, monkeypatch):
        """FR-SEC-8: ``set_password`` clears the cache (the stored hash is left as it was here to show it)."""
        monkeypatch.setattr(config_store, 'set', lambda _dotted, _value: None)
        monkeypatch.setattr(password_hash, 'make', lambda _password: 'not-written')
        assert _check(page_password) == auth.OK
        auth.set_password(page_password)
        assert _check(page_password) == auth.OK
        assert len(pbkdf2_calls) == 2


class TestStillCurrent:

    @pytest.mark.parametrize('cached', [False, True], ids=['pbkdf2', 'cache-hit'])
    @pytest.mark.parametrize('new_value', [fast_hash('another-password'), '{not json'], ids=['changed', 'unreadable'])
    def test_password_changed_or_unreadable_after_the_check_is_bad(self, page_password, worker_home, monkeypatch,
                                                                   cached, new_value):
        """FR-SEC-8 step 4, chunk 4 review (concurrency): a right password whose stored hash was replaced (or the
        file became unreadable) before the re-read is ``BAD``; the attempt stays forgiven (not a failure)."""
        real = auth.current_hash
        reads = []

        def current_hash():
            reads.append(None)
            if len(reads) == 2:  # the step 4 re-read: another process replaced config.json meanwhile
                if new_value.startswith('{'):
                    (worker_home / 'config.json').write_text(new_value, encoding='utf-8')
                else:
                    write_hash(worker_home, new_value)
            return real()
        if cached:
            auth._cache.store(real(), page_password)
        monkeypatch.setattr(auth, 'current_hash', current_hash)

        assert _check(page_password) == auth.BAD
        assert len(reads) == 2
        assert auth._limit._attempts == {}


class TestParallelChecks:

    def test_a_third_parallel_pbkdf2_check_is_busy(self, page_password, held_checks):
        """FR-SEC-8 step 3: at most two PBKDF2 checks at once; a third gets ``BUSY`` (429)."""
        results = []
        threads = [_in_thread(lambda client=client: results.append(_check('wrong', client)))
                   for client in (LAN_A, LAN_B)]
        try:
            assert held_checks.inside.acquire(timeout=WAIT) and held_checks.inside.acquire(timeout=WAIT)
            assert _check('third-guess', '192.168.1.22') == auth.BUSY
        finally:
            held_checks.release.set()
            for thread in threads:
                thread.join(WAIT)
        assert results == [auth.BAD, auth.BAD]


class TestStoredHash:

    def test_no_password_is_no_password(self, no_page_password, pbkdf2_calls):
        """FR-WK-4: no default password; with none stored the answer is ``NO_PASSWORD`` (403) without hashing."""
        assert _check('anything') == auth.NO_PASSWORD
        assert pbkdf2_calls == []

    def test_unusable_stored_value_counts_as_none_and_is_logged_once(self, worker_home, caplog):
        """FR-WK-3: a hand-edited or old-format value is ignored (one ERROR line), never checked."""
        write_hash(worker_home, 'a3f1c2-old-100000-round-hex')
        assert _check('anything') == auth.NO_PASSWORD and _check('anything') == auth.NO_PASSWORD
        assert [r.levelname for r in caplog.records if r.name == auth.__name__] == ['ERROR']

    def test_unreadable_settings_file_is_unreadable(self, worker_home):
        """FR-SEC-8: a settings file that cannot be read gives ``UNREADABLE`` (503), never a pass."""
        (worker_home / 'config.json').write_text('{not json', encoding='utf-8')
        assert _check('anything') == auth.UNREADABLE
