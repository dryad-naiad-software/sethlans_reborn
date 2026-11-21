# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for :mod:`workers.video_queue` and the item rules of
:mod:`workers.services.sequence_items` (Unreal sequence spec D16, FR-SV-2,
FR-SV-7, NF-3).

Covers: the queue accepts nothing until started; a waiting key is not
queued twice; the thread runs each key with the boot id, survives a task
that raises, and ``stop_for_tests`` joins it and forgets waiting keys;
``start`` runs once per process; keys are queued after commit.  Item rules:
what a submission asks for (EXR gives frames only; merging needs video and
sound) and how a whole item follows its inputs.
"""

import threading
from types import SimpleNamespace

import pytest

from workers import video_queue
from workers.services.sequence_items import _whole_next, wants


@pytest.fixture(autouse=True)
def _clean_queue():
    video_queue.stop_for_tests()
    yield
    video_queue.stop_for_tests()


class TestQueue:

    def test_not_started_accepts_nothing(self):
        assert video_queue.enqueue((1, None, 'video')) is False
        assert video_queue.boot_id() == ''

    def test_thread_runs_keys_and_survives_a_raising_task(self, monkeypatch):
        seen, done = [], threading.Event()

        def fake(key, boot):
            seen.append((key, boot, threading.current_thread().name))
            if key[1] == 1:
                raise RuntimeError('boom')
            done.set()

        monkeypatch.setattr('workers.video_sequence.run_task', fake)
        assert video_queue.start('boot-a') is True
        assert video_queue.start('boot-b') is False
        assert video_queue.enqueue((7, 1, 'video')) is True
        assert video_queue.enqueue((7, 2, 'sound')) is True
        assert done.wait(5)
        assert [(key, boot) for key, boot, _name in seen] == [((7, 1, 'video'), 'boot-a'), ((7, 2, 'sound'), 'boot-a')]
        assert {name for _k, _b, name in seen} == {video_queue.THREAD_NAME}
        thread = video_queue._state['thread']
        video_queue.stop_for_tests()
        assert not thread.is_alive()
        assert video_queue.is_accepting() is False

    def test_waiting_key_is_not_queued_twice(self, monkeypatch):
        ran = []
        monkeypatch.setattr('workers.video_sequence.run_task', lambda key, boot: ran.append(key))
        video_queue.start_manual_for_tests('boot-m')
        assert video_queue.enqueue((3, None, 'video')) is True
        assert video_queue.enqueue((3, None, 'video')) is False
        assert video_queue.enqueue((3, 1, 'video')) is True
        assert video_queue.run_pending_for_tests() == 2
        assert ran == [(3, None, 'video'), (3, 1, 'video')]
        assert video_queue.enqueue((3, None, 'video')) is True

    def test_stop_forgets_waiting_keys(self):
        video_queue.start_manual_for_tests('boot-m')
        video_queue.enqueue((4, 1, 'video'))
        video_queue.stop_for_tests()
        video_queue.start_manual_for_tests('boot-n')
        assert video_queue.run_pending_for_tests() == 0

    def test_keys_are_queued_on_commit(self, monkeypatch):
        callbacks = []
        monkeypatch.setattr('workers.video_queue.transaction.on_commit',
                            lambda fn, robust=False: callbacks.append((fn, robust)))
        video_queue.start_manual_for_tests('boot-m')
        video_queue.enqueue_on_commit([(5, 1, 'video'), (5, 2, 'video')])
        video_queue.enqueue_on_commit([])
        assert len(callbacks) == 1 and callbacks[0][1] is True
        assert video_queue._tasks.empty()
        callbacks[0][0]()
        assert video_queue._tasks.qsize() == 2

    def test_a_failing_connection_close_does_not_end_the_thread(self, monkeypatch):
        """Review: closing old connections raises; the thread keeps running the next keys."""
        seen, done = [], threading.Event()
        monkeypatch.setattr('workers.video_queue.close_old_connections', lambda: (_ for _ in ()).throw(
            RuntimeError('connection gone')))
        monkeypatch.setattr('workers.video_sequence.run_task', lambda key, boot: (seen.append(key), done.set()))
        video_queue.start('boot-c')
        video_queue.enqueue((8, 1, 'video'))
        assert done.wait(5)
        done.clear()
        video_queue.enqueue((8, 2, 'video'))
        assert done.wait(5) and seen == [(8, 1, 'video'), (8, 2, 'video')]
        assert video_queue._state['thread'].is_alive()

    def test_a_dead_thread_is_started_again(self, monkeypatch):
        """Review: should the thread die, the next key starts a new one instead of piling up."""
        done = threading.Event()
        monkeypatch.setattr('workers.video_sequence.run_task', lambda key, boot: done.set())
        video_queue.start('boot-d')
        old = video_queue._state['thread']
        video_queue._tasks.put(video_queue._STOP)
        old.join(5)
        assert not old.is_alive() and video_queue.is_accepting()
        assert video_queue.enqueue((9, 1, 'video')) is True
        assert done.wait(5)
        assert video_queue._state['thread'] is not old and video_queue._state['thread'].is_alive()


def _submission(video=True, frame_format='PNG', sound=True, merge=True):
    return SimpleNamespace(video_settings=dict(container='mp4') if video else None, frame_format=frame_format,
                           sound=sound, merge_sound=merge)


class TestItemRules:

    @pytest.mark.parametrize('submission, expected', [
        (_submission(), {'video': True, 'sound': True, 'sound_video': True}),
        (_submission(frame_format='EXR'), {'video': False, 'sound': True, 'sound_video': False}),
        (_submission(sound=False), {'video': True, 'sound': False, 'sound_video': False}),
        (_submission(merge=False), {'video': True, 'sound': True, 'sound_video': False}),
        (_submission(video=False), {'video': False, 'sound': True, 'sound_video': False}),
    ], ids=['all', 'exr', 'no-sound', 'no-merge', 'no-video'])
    def test_wants(self, submission, expected):
        assert wants(submission) == expected

    @pytest.mark.parametrize('current, wanted, done, failed, expected', [
        (None, True, True, 0, ('PENDING', '')),
        (None, True, False, 0, None),
        (None, True, False, 2, ('ERROR', 'm')),
        ('PENDING', True, False, 1, ('ERROR', 'm')),
        ('PENDING', True, True, 0, None),
        ('ASSEMBLING', True, False, 1, None),
        ('DONE', True, True, 0, None),
        ('ERROR', True, True, 0, None),
        (None, False, True, 0, None),
    ])
    def test_whole_item_follows_its_inputs(self, current, wanted, done, failed, expected):
        assert _whole_next(current, wanted, done, failed, 'm') == expected
