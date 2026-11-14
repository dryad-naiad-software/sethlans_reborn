# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Blender API snapshot test (Unreal foundation FR-T-2, chunk 0).

Captured on master after the prerequisite fixes #215-#221 and #223, before
the renderer abstraction lands.  Every later chunk must keep this test
green without editing it or the snapshot: it is the guard for "Blender
behavior unchanged" (FR-MR-10, AC-3).

The scenario (``blender_invariance_scenario.py``) sends the FR-T-2
requests through ``APIClient``: project, asset, job, tiled-job and
animation creates, heartbeat registration and heartbeats, worker polls
and their errors, the admin job list, claims and a worker PATCH.  The
comparator (``blender_invariance_normalize.py``) drops the new response
keys per serializer kind and normalizes ids by row name, then compares
canonical JSON text, so the same requests must produce the same status
codes and type-exact bodies.

Documented exception (D7, FR-RT-15): worker-poll lists are compared as
sets keyed by job ``name``.  Their pre-change (newest first) order is
kept in the snapshot's ``documented_exceptions`` entry as metadata only;
the oldest-first order is asserted by ``test_poll_fifo_order.py``.

The snapshot is ``snapshots/renderer_blender_invariance.json``.  It is
regenerated only by hand on pre-change master with
``SETHLANS_UPDATE_BLENDER_SNAPSHOT=1``.  Regeneration refuses to run in
CI and fails if any raw response already carries a new key name, so it
cannot silently absorb a chunk 1+ change.
"""

import json
import os
from pathlib import Path

import pytest

from workers.views import heartbeat as heartbeat_mod

from .blender_invariance_normalize import (
    canonical, keyed_by_name, new_key_paths, normalize, normalize_pattern,
    strip_new_keys,
)
from .blender_invariance_scenario import WORKER_HOSTNAME, Recorder, run_scenario

SNAPSHOT_PATH = Path(__file__).parent / 'snapshots' / 'renderer_blender_invariance.json'
UPDATE_ENV = 'SETHLANS_UPDATE_BLENDER_SNAPSHOT'

POLL_ORDER_EXCEPTION = {
    'id': 'worker-poll-order',
    'requirement': 'FR-RT-15 / D7',
    'metadata_only': True,
    'text': (
        'Deliberate change, not a regression: worker polls return QUEUED '
        'jobs oldest first (submitted_at, then id) after FR-RT-15 / D7. '
        'Captures marked "set-by-name" are compared as sets keyed by job '
        'name. pre_change_order records the newest-first order of master '
        'before chunk 1; nothing asserts it. The new order is asserted '
        'by test_poll_fifo_order.py (FR-T-3).'
    ),
}


@pytest.fixture
def invariance_env(settings, tmp_path, monkeypatch):
    """Isolate uploads and the setup-complete flag from the machine.

    Uploaded ``.blend`` files go to a temporary ``MEDIA_ROOT``.  The
    heartbeat's ``manager_setup_complete`` reads a sentinel in the
    manager data dir and caches a True result for the process, so the
    data dir is an empty temporary one and the cache is reset: the flag
    is always False here, whatever the machine or earlier tests did.
    """
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    monkeypatch.setenv('SETHLANS_MANAGER_DATA_DIR', str(data_dir))
    heartbeat_mod._reset_setup_complete_cache()
    yield
    heartbeat_mod._reset_setup_complete_cache()


def _snapshot_document(captures, poll_orders):
    exception = dict(POLL_ORDER_EXCEPTION, pre_change_order=poll_orders)
    return {
        'description': (
            'Blender API snapshot (Unreal foundation FR-T-2). Written by '
            'test_renderer_blender_invariance.py; do not edit by hand.'
        ),
        'documented_exceptions': [exception],
        'captures': captures,
    }


def _write_snapshot(document):
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(canonical(document, indent=2) + '\n', encoding='utf-8', newline='\n')


def _load_snapshot():
    return json.loads(SNAPSHOT_PATH.read_text(encoding='utf-8'))


def _regenerate(rec):
    """Rewrite the snapshot from *rec*; only valid on pre-change master."""
    if os.environ.get('CI'):
        pytest.fail(f'{UPDATE_ENV}=1 is not allowed in CI; regenerate by hand on pre-change master.')
    raw_paths = rec.raw_new_key_paths()
    assert not raw_paths, (
        'raw responses already contain new key names, so this is not '
        f'pre-change master; refusing to regenerate: {raw_paths}'
    )
    _write_snapshot(_snapshot_document(*rec.finish()))
    pytest.skip(f'{UPDATE_ENV}=1: snapshot rewritten at {SNAPSHOT_PATH}')


@pytest.mark.django_db
class TestRendererBlenderInvarianceIntegration:
    """The Blender-facing API answers exactly as the snapshot records."""

    def test_blender_api_matches_snapshot(
        self, invariance_env, admin_client, make_worker_client,
    ):
        worker, worker_client, _ = make_worker_client(WORKER_HOSTNAME)
        rec = run_scenario(admin_client, worker_client, worker)
        if os.environ.get(UPDATE_ENV) == '1':
            _regenerate(rec)

        captures, _ = rec.finish()
        expected = _load_snapshot()['captures']
        assert sorted(captures) == sorted(expected), 'capture names differ'
        mismatched = [
            name for name in expected
            if canonical(captures[name]) != canonical(expected[name])
        ]
        for name in mismatched[:1]:
            assert canonical(captures[name], 2) == canonical(expected[name], 2), (
                f'{name} differs from the snapshot; all mismatches: {mismatched}'
            )


class TestBlenderSnapshotDocument:
    """The committed snapshot carries the D7 exception and no new keys."""

    def test_poll_captures_carry_documented_order(self):
        document = _load_snapshot()
        (exception,) = document['documented_exceptions']
        assert exception['requirement'] == 'FR-RT-15 / D7'
        assert 'FR-RT-15' in exception['text'] and 'D7' in exception['text']
        assert exception['metadata_only'] is True

        unordered = {
            name: entry for name, entry in document['captures'].items()
            if entry.get('compare') == 'set-by-name'
        }
        assert unordered, 'no worker-poll capture is marked set-by-name'
        assert all(name.startswith('poll_') for name in unordered)
        assert sorted(exception['pre_change_order']) == sorted(unordered)
        for name, entry in unordered.items():
            order = exception['pre_change_order'][name]
            assert sorted(order) == sorted(entry['body']), name

    def test_snapshot_has_no_new_key_names(self):
        """No new key name anywhere in the committed baseline.

        The comparator strips new keys only where a serializer adds them,
        and never inside free-form JSON or error bodies.  This checks the
        whole document at every depth, so a baseline that was captured
        after chunk 1 (or hand-edited to include a new key) fails here.
        """
        assert new_key_paths(_load_snapshot()) == []


class TestBlenderSnapshotComparator:
    """The comparator ignores new keys and poll order, nothing else."""

    LABELS = {
        ('job', '7'): '<job:JobA>', ('job', '9'): '<job:JobB>',
        ('asset', '3'): '<asset:SceneA>', ('tiledjob', 'abc'): '<tiledjob:T>',
    }

    @staticmethod
    def _poll(*names_and_ids):
        return [
            {'id': pk, 'name': name, 'status': 'QUEUED', 'assigned_worker': None,
             'is_paused': True, 'render_settings': {'render.border_min_x': 0.0},
             'submitted_at': '2026-11-14T10:00:00.123456Z',
             'asset': {'id': 3, 'name': 'SceneA', 'project_details': {'name': 'P'},
                       'blend_file': 'http://testserver/media/assets/0a1b2c3d/9f8e7d6c.blend'}}
            for name, pk in names_and_ids
        ]

    def _compare(self, jobs):
        normalized = normalize(strip_new_keys(jobs, 'job'), 'job', self.LABELS)
        return canonical(keyed_by_name(normalized))

    def test_order_and_new_keys_are_ignored(self):
        before = self._poll(('JobB', 9), ('JobA', 7))
        after = self._poll(('JobA', 7), ('JobB', 9))
        for job in after:
            job.update(renderer='BLENDER', engine_build='', routing_note=None)
            job['asset'].update(source_type='blend_file', archive_path='')
            job['asset']['project_details'].update(renderer='BLENDER', engine_build='')
        assert self._compare(before) == self._compare(after)
        assert '"id": "<job:JobA>"' in self._compare(before)

    def test_changed_value_is_detected(self):
        before = self._poll(('JobA', 7), ('JobB', 9))
        after = self._poll(('JobA', 7), ('JobB', 9))
        after[1]['status'] = 'RENDERING'
        assert self._compare(before) != self._compare(after)

    def test_missing_job_is_detected(self):
        before = self._poll(('JobA', 7), ('JobB', 9))
        assert self._compare(before) != self._compare(before[:1])

    def test_type_changes_are_detected(self):
        before = self._poll(('JobA', 7))
        bool_to_int = self._poll(('JobA', 7))
        bool_to_int[0]['is_paused'] = 1
        float_to_int = self._poll(('JobA', 7))
        float_to_int[0]['render_settings']['render.border_min_x'] = 0
        assert self._compare(before) != self._compare(bool_to_int)
        assert self._compare(before) != self._compare(float_to_int)

    def test_new_key_inside_free_form_json_is_detected(self):
        before = self._poll(('JobA', 7))
        after = self._poll(('JobA', 7))
        after[0]['render_settings']['renderer'] = 'BLENDER'
        assert self._compare(before) != self._compare(after)

    def test_raw_new_key_guard_sees_every_depth(self):
        class _Response:
            status_code = 200
            content = b'[{"name": "JobA", "render_settings": {"renderer": 1}}]'

        rec = Recorder()
        rec.capture('poll_x', _Response(), 'job', unordered=True)
        assert rec.raw_new_key_paths() == ['poll_x[0].render_settings.renderer']

    def test_native_separator_is_canonicalized(self):
        pattern = os.sep.join(['tiled_jobs', 'abc', 'tile_0_0_####.png'])
        assert normalize_pattern(pattern, self.LABELS) == 'tiled_jobs/<tiledjob:T>/tile_0_0_####.png'

    def test_non_native_separator_fails(self):
        other = '/' if os.sep == '\\' else '\\'
        with pytest.raises(AssertionError, match='os.sep'):
            normalize_pattern(other.join(['tiled_jobs', 'abc', 'tile.png']), self.LABELS)
        with pytest.raises(AssertionError, match='os.sep'):
            normalize_pattern(os.sep.join(['tiled_jobs', 'abc', 'sub', 'tile.png']), self.LABELS)
