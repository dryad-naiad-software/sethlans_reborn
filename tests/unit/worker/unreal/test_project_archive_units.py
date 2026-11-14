# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Pure pieces of the project-archive pipeline (Unreal foundation FR-PA-6
name rules, FR-PA-10 populate locks, FR-PA-11 ledger, ``.complete.json``
parsing, cancel/deadline checks).
"""
import threading

import pytest

from sethlans_worker_agent.project_archive import entry_record, layout, ledger, zip_rules
from sethlans_worker_agent.project_archive.abort import Abort
from sethlans_worker_agent.project_archive.errors import AbortedError, PrepTimeoutError
from sethlans_worker_agent.project_archive.populate_lock import PopulateLocks

ACCEPT = ['Demo/Demo.uproject', 'Demo/Content/Maps/Main.umap', 'a.b.c', 'Demo/CONSOLE.txt', 'Demo/COM10',
          'Demo/x~y', 'ß/Straße.txt']
REJECT = ['', '/a', '//host/share', 'C:/x', 'C:x', '..', 'a/../b', 'a/./b', 'a//b', 'a/', 'a\x00b', 'a\x1fb',
          'a<b', 'a>b', 'a:b', 'a"b', 'a|b', 'a?b', 'a*b', 'CON', 'con.txt', 'Demo/NUL.tar.gz', 'aux',
          'CONOUT$', 'COM1', 'LPT9.log', 'COM\u00b2', 'LPT\u00b3.x', 'CON .txt', 'a.', 'a ', 'x' * 256,
          'PROGRA~1', 'Demo/ABCDEF~12.txt']


@pytest.mark.parametrize('name', ACCEPT)
def test_names_accepted(name):
    assert zip_rules.normalize_relpath(name) == name


@pytest.mark.parametrize('name', REJECT)
def test_names_rejected(name):
    with pytest.raises(ValueError):
        zip_rules.normalize_relpath(name)


def test_backslashes_and_directory_slash_are_normalized():
    assert zip_rules.normalize_relpath('Demo\\Content\\', is_dir=True) == 'Demo/Content'
    with pytest.raises(ValueError):
        zip_rules.normalize_relpath('Demo\\..\\x')


def test_fold_key_is_length_preserving():
    assert zip_rules.fold_key('Demo/a.TXT') == zip_rules.fold_key('DEMO/A.txt')
    assert zip_rules.fold_key('ß') != zip_rules.fold_key('ss')


def test_reservation_consume_and_release():
    lock, book = threading.Lock(), ledger.ByteLedger()
    book.in_flight_reserved = 100
    reservation = ledger.Reservation(lock, book, 100)
    reservation.consume(30)
    reservation.consume(500)  # never below zero
    assert reservation.remaining == 0 and book.in_flight_reserved == 0
    book.in_flight_reserved = 50
    with ledger.Reservation(lock, book, 50) as other:
        other.consume(20)
        assert book.in_flight_reserved == 30
    assert book.in_flight_reserved == 0


def test_ledger_fit_rules():
    book = ledger.ByteLedger(pristine_total=80, in_flight_reserved=10)
    assert book.disk_fits(free=100, required=40, min_free=50)
    assert not book.disk_fits(free=100, required=41, min_free=50)
    assert book.cache_fits(None, cache_max=0)
    assert book.cache_fits(20, cache_max=100) and not book.cache_fits(21, cache_max=100)


def test_abort_causes_and_deadline():
    cancel, shutdown = threading.Event(), threading.Event()
    Abort(cancel, shutdown).check()
    cancel.set()
    with pytest.raises(AbortedError) as caught:
        Abort(cancel, shutdown).check()
    assert caught.value.cause == 'canceled'
    shutdown.set()
    assert Abort(cancel, shutdown).cause() == 'shutdown'
    with pytest.raises(PrepTimeoutError):
        Abort(deadline=5.0, clock=lambda: 6.0).check()


def test_populate_locks_are_dropped_after_use():
    locks = PopulateLocks(poll_seconds=0.01)
    with locks.hold('a' * 64):
        assert locks.known() == 1 and locks.holds_any()
    assert locks.known() == 0 and not locks.holds_any()


def test_populate_lock_waiter_aborts():
    locks = PopulateLocks(poll_seconds=0.01)
    held, done = threading.Event(), threading.Event()

    def holder():
        with locks.hold('a' * 64):
            held.set()
            done.wait(5)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    held.wait(5)
    shutdown = threading.Event()
    shutdown.set()
    with pytest.raises(AbortedError):
        with locks.hold('a' * 64, Abort(shutdown=shutdown)):
            pass
    done.set()
    thread.join(5)
    assert locks.known() == 0


def _record(**overrides):
    data = {'schema': 1, 'sha256': 'a' * 64, 'source': {'unc': '\\\\h\\s\\x.zip', 'size': 1, 'mtime_ns': 2},
            'total_bytes': 3, 'dirs': ['Demo'], 'files': [['Demo/a.txt', 3, 4, 'b' * 64, 0]]}
    data.update(overrides)
    return data


def test_record_parses():
    record = entry_record.parse_record(_record())
    assert record.sha256 == 'a' * 64 and record.files[0].relpath == 'Demo/a.txt'


@pytest.mark.parametrize('overrides', [
    {'schema': 2}, {'sha256': 'A' * 64}, {'total_bytes': 4}, {'dirs': ['../x']},
    {'files': [['../a.txt', 3, 4, 'b' * 64, 0]]}, {'files': [['Demo/a.txt', -1, 4, 'b' * 64, 0]]},
    {'files': [['Demo/a.txt', 3, 4, 'xyz', 0]]}, {'files': [['Demo\\a.txt', 3, 4, 'b' * 64, 0]]},
    {'files': [['Demo/a.txt', True, 4, 'b' * 64, 0]]}, {'source': None},
    {'files': [['Demo/a.txt', 3, 4, 'b' * 64, 0], ['Demo/a.txt', 0, 4, 'b' * 64, 0]]},
])
def test_record_rejects(overrides):
    with pytest.raises(entry_record.RecordError):
        entry_record.parse_record(_record(**overrides))


def test_layout_names(tmp_path):
    cache = layout.CacheLayout(tmp_path)
    sha, ov = '0123456789abcdef' + 'f' * 48, '1a2b3c4d' + 'e' * 56
    assert cache.entry_dir(sha) == tmp_path / 'p' / '0123456789abcdef'
    assert cache.work_base_name(sha, ov, 0) == '0123456789abcdef1a2b3c4d-s0'
    assert cache.work_base_name(sha, '', 0) == '0123456789abcdef00000000-s0'
    assert cache.overlay_dir(42, '1a2b3c4d') == tmp_path / 'o' / '42-1a2b3c4d'
    assert layout.safe_job_token('1a2b3c4d') == '1a2b3c4d'
    for bad in ('1A2B3C4D', '1a2b3c4', '../x', ''):
        with pytest.raises(ValueError):
            layout.safe_job_token(bad)
