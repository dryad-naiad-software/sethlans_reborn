# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""``populate_cache()`` around ``list_series`` (#113, BS-REL-9).

The serving process's start thread calls ``populate_cache()``, which asks
``shared.blender_sources.list_series`` for the series on the first source
that lists one. Guarded here:

1. #113: ``populate_cache`` returns within
   :data:`POPULATE_CACHE_BUDGET_SECONDS` even when the lookup hangs, marks
   the cache ready and keeps the series it had.
2. BS-REL-9: the cache holds exactly what ``list_series`` returned; a
   ``BlenderSourcesError`` marks it ready and keeps the old series.

The per-request listing timeout is guarded with the package
(``tests/unit/shared/test_blender_sources_release_list.py``).
"""

from __future__ import annotations

import threading

import pytest

from shared.blender_sources import AllSourcesFailedError
from workers.utils import blender_series_cache as cache

INNER_THREAD = 'blender-series-cache-populate-inner'


@pytest.fixture
def cache_state():
    """Start from a known cache (old series, not ready) and restore the module state afterwards."""
    with cache._cache_lock:
        saved = (cache._cached_series, cache._cache_ready)
        cache._cached_series, cache._cache_ready = ['4.2', '4.5'], False
    yield
    with cache._cache_lock:
        cache._cached_series, cache._cache_ready = saved


@pytest.fixture
def hung_lookup(mocker):
    """``list_series`` blocks until the test ends; the inner thread is released and joined at teardown."""
    release = threading.Event()

    def hang():
        release.wait()
        raise AllSourcesFailedError('Blender release list (index)', [])
    mocker.patch.object(cache, 'list_series', side_effect=hang)
    yield
    release.set()
    for thread in threading.enumerate():
        if thread.name == INNER_THREAD:
            thread.join(timeout=5)


def test_returns_within_budget_when_the_lookup_hangs(mocker, cache_state, hung_lookup):
    """#113: a hung lookup cannot hold ``populate_cache``; the cache is ready and keeps its series."""
    mocker.patch.object(cache, 'POPULATE_CACHE_BUDGET_SECONDS', 0.05)

    cache.populate_cache()

    assert cache.get_available_series() == {'series': ['4.2', '4.5'], 'cache_ready': True}


def test_stores_the_series_list_series_returned(mocker, cache_state):
    """BS-REL-9: the cache is the index lookup's answer (series only, not the whole tree)."""
    lookup = mocker.patch.object(cache, 'list_series', return_value=['4.5', '5.0'])

    cache.populate_cache()

    lookup.assert_called_once_with()
    assert cache.get_available_series() == {'series': ['4.5', '5.0'], 'cache_ready': True}


def test_a_source_error_keeps_the_old_series(mocker, cache_state):
    """BS-REL-9, BS-LOG-2: every source failing marks the cache ready and keeps what it had."""
    mocker.patch.object(cache, 'list_series', side_effect=AllSourcesFailedError(
        'Blender release list (index)', [('https://download.blender.org/release/', 'HTTP 403')]))

    cache.populate_cache()

    assert cache.get_available_series() == {'series': ['4.2', '4.5'], 'cache_ready': True}
