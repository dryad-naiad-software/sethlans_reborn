# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Contract: what a pilot ran with (gpu-memory-reader chunk 6, FR-GC-2, FR-GC-4,
FR-GC-7, FR-GC-8) is the same set of words on both sides.

* The worker's allowlists (``queue_prep.GAME_OVERRIDES`` /
  ``TEXTURE_STREAMING``), the words the UE-side facts module can return
  (``sethlans_preset_facts.py``, read statically: it imports ``unreal``) and
  ``pilot.report_body``'s defaults equal the manager's
  ``UNREAL_GAME_OVERRIDES`` / ``UNREAL_TEXTURE_STREAMING``, in both
  directions.
* Every allowed pair, sent in the worker's own ``pilot.report_body``, is
  accepted by the real ``pilot-result/`` endpoint and stored; an unknown
  word is 400 and stores nothing.
"""

import ast
import inspect
import itertools
import json

import pytest

from shared.unreal.validation import overrides_sha256
from sethlans_worker_agent.gpu_memory.types import make_gpu
from sethlans_worker_agent.unreal_executor import job_dir, pilot, queue_prep
from workers.constants import UNREAL_GAME_OVERRIDES, UNREAL_TEXTURE_STREAMING

from .unreal_helpers import BUILD, card_key_of, unreal_asset
from .unreal_pilot_helpers import claimed_pilot, post_result, record_of

FACTS_PATH = job_dir.SCRIPTS_SOURCE / job_dir.FACTS_SCRIPT
PAIRS = list(itertools.product(UNREAL_GAME_OVERRIDES, UNREAL_TEXTURE_STREAMING))


def _strings(node):
    return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _assigned(tree, name):
    """The value node assigned to the module-level ``name``."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and [t.id for t in node.targets if isinstance(t, ast.Name)] == [name]:
            return node.value
    raise AssertionError(f'{name} not found in {FACTS_PATH.name}')


def _facts_words():
    """``(game_overrides, texture_streaming)`` word sets the facts module can return.

    Its fact-returning functions end in ``(..., game_overrides, texture_streaming)`` tuples;
    the ``UNKNOWN`` pair and the ``_LEGACY_STREAMING`` table give the rest.
    """
    tree = ast.parse(FACTS_PATH.read_text(encoding='utf-8'))
    unknown = _assigned(tree, 'UNKNOWN').elts
    game, streaming = _strings(unknown[0]), _strings(unknown[1])
    streaming |= {pair.elts[1].value for pair in _assigned(tree, '_LEGACY_STREAMING').elts}
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple) and len(node.value.elts) >= 2:
            game |= _strings(node.value.elts[-2])
            streaming |= _strings(node.value.elts[-1])
    return game, streaming


def _report(game_overrides, texture_streaming):
    """The worker's own pilot-result body, as the manager receives it (through JSON)."""
    metrics = pilot.PilotMetrics(
        peak_mb=8102, peak_source='csv', card_delta_peak_mb=7930, csv_local_used_peak_mb=8102,
        baseline_used_mb=945, baseline_drift_mb=12, peak_phase='render', gpu_index=0,
        max_sample_gap_s=1.2, process_peak_mb=6144)
    gpu = make_gpu(0, 'GPU', 32607, 0, 0, card_key=card_key_of(0))
    body = pilot.report_body(metrics, gpu, prep_s=24.1, warmup_s=800.2, render_s=1380.5, frames=23,
                             engine_build=BUILD, overrides_sha256=overrides_sha256({}), summary='pilot_frames=23',
                             game_overrides=game_overrides, texture_streaming=texture_streaming)
    return json.loads(json.dumps(body))


class TestUnrealGameOverridesWordsIntegration:

    def test_worker_allowlists_equal_the_manager_words(self):
        assert set(queue_prep.GAME_OVERRIDES) == set(UNREAL_GAME_OVERRIDES)
        assert set(queue_prep.TEXTURE_STREAMING) == set(UNREAL_TEXTURE_STREAMING)
        assert queue_prep.UNKNOWN in UNREAL_GAME_OVERRIDES and queue_prep.UNKNOWN in UNREAL_TEXTURE_STREAMING

    def test_facts_module_words_equal_the_manager_words(self):
        game, streaming = _facts_words()
        assert game == set(UNREAL_GAME_OVERRIDES)
        assert streaming == set(UNREAL_TEXTURE_STREAMING)

    def test_report_body_defaults_are_manager_words(self):
        params = inspect.signature(pilot.report_body).parameters
        assert params['game_overrides'].default in UNREAL_GAME_OVERRIDES
        assert params['texture_streaming'].default in UNREAL_TEXTURE_STREAMING


@pytest.fixture
def ready_pilot(db, admin_client, worker_with_token):
    """A pilot claimed by a ready worker; returns ``(pilot, client)``."""
    worker, client = worker_with_token
    return claimed_pilot(admin_client, unreal_asset(), worker, client), client


@pytest.mark.django_db
class TestUnrealGameOverridesRoundTripIntegration:

    @pytest.mark.parametrize('game_overrides,texture_streaming', PAIRS)
    def test_every_allowed_pair_is_accepted_and_stored(self, ready_pilot, game_overrides, texture_streaming):
        job, client = ready_pilot
        resp = post_result(client, job.pk, _report(game_overrides, texture_streaming))
        assert resp.status_code == 200, resp.data
        record = record_of(job)
        assert (record.game_overrides, record.texture_streaming) == (game_overrides, texture_streaming)

    @pytest.mark.parametrize('field', ['game_overrides', 'texture_streaming'])
    def test_an_unknown_word_is_400_and_stores_nothing(self, ready_pilot, field):
        job, client = ready_pilot
        body = _report('preset', 'fully_load')
        body[field] = 'streaming_on'
        resp = post_result(client, job.pk, body)
        assert resp.status_code == 400, resp.data
        assert field in resp.data
        record = record_of(job)
        assert (record.peak_mb, record.game_overrides, record.texture_streaming) == (None, '', '')
        job.refresh_from_db()
        assert job.status == 'RENDERING'
