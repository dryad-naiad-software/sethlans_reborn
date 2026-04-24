# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Regression tests for issue #113 — unbounded populate_cache().

The ``WorkersConfig.ready`` hook spawns a daemon thread that calls
``populate_cache()`` → ``get_blender_releases()``. Historically that
chain had only per-request ``requests.get(..., timeout=...)`` guards,
which are reset on each received byte. A slow-drip or dead-in-flight
connection to ``download.blender.org`` could keep the thread alive
indefinitely, and the 1.8 MB SQLite WAL observed on a schema-only DB
strongly suggested the hung thread was starving downstream writers
(the reported symptom was ``/api/setup/session/`` hanging forever).

These tests lock in two guarantees:

1. ``populate_cache`` cannot run for longer than
   :data:`POPULATE_CACHE_BUDGET_SECONDS`, even when the underlying
   parser is stuck. It must return with ``_cache_ready == True``.
2. Every ``requests.get`` call made by the parser is invoked with
   an explicit non-``None`` ``timeout`` kwarg — so future drift that
   accidentally removes a timeout fails loudly at test time instead
   of in production.
"""

from __future__ import annotations

import time

import pytest

from workers.utils import blender_release_parser as parser
from workers.utils import blender_series_cache as cache


# ---------------------------------------------------------------------
# #113 — populate_cache respects the overall wall-clock budget.
# ---------------------------------------------------------------------

class TestPopulateCacheBudget:
    """``populate_cache`` must bail out within its budget even when the
    parser it delegates to is fully hung. The production path uses the
    real 60-second budget; tests patch it down to a second or two so
    the suite stays fast while still exercising the real code path."""

    def _reset_cache_state(self):
        with cache._cache_lock:
            cache._cached_series = []
            cache._cache_ready = False

    def test_returns_within_budget_when_parser_hangs(self, mocker):
        """Simulate a dead download.blender.org by mocking
        ``get_blender_releases`` to sleep far past the budget. The
        outer ``populate_cache`` must still return promptly and mark
        the cache ready, so callers of ``get_available_series`` stop
        waiting on a flag that would otherwise never flip."""
        self._reset_cache_state()
        mocker.patch.object(cache, 'POPULATE_CACHE_BUDGET_SECONDS', 1.0)

        # Parser sleeps 120s — orders of magnitude past the budget,
        # matching the "18 minutes of silence" observed in the real
        # incident.
        mocker.patch.object(
            cache, 'get_blender_releases',
            side_effect=lambda: time.sleep(120),
        )

        start = time.monotonic()
        cache.populate_cache()
        elapsed = time.monotonic() - start

        # Budget + generous slack for thread startup / GIL scheduling.
        assert elapsed < 5.0, (
            f"populate_cache ran for {elapsed:.2f}s; budget is 1.0s. "
            "Issue #113 regression — wall-clock guard is broken."
        )
        assert cache._cache_ready is True, (
            "On budget timeout, populate_cache must still mark the "
            "cache ready so /api/ endpoints and the settings page "
            "don't appear stuck. Mirrors the existing exception "
            "branch."
        )

    def test_returns_promptly_on_fast_success(self, mocker):
        """Sanity — the budget path must not penalise the happy case.
        A fast parser should finish long before the budget elapses and
        should populate the cache with the series it returned."""
        self._reset_cache_state()
        mocker.patch.object(cache, 'POPULATE_CACHE_BUDGET_SECONDS', 5.0)
        mocker.patch.object(
            cache, 'get_blender_releases',
            return_value={'4.5.8': {}, '4.5.9': {}, '4.6.0': {}},
        )

        start = time.monotonic()
        cache.populate_cache()
        elapsed = time.monotonic() - start

        assert elapsed < 2.0
        assert cache._cache_ready is True
        assert cache._cached_series == ['4.5', '4.6']

    def test_budget_exceeded_preserves_previously_cached_series(
        self, mocker,
    ):
        """If a refresh blows past the budget, the operator-visible
        series list must not be wiped — we keep the last known-good
        data rather than presenting an empty dropdown in the UI.
        Matches the exception-branch contract in _populate_cache_body."""
        with cache._cache_lock:
            cache._cached_series = ['4.5', '4.6']
            cache._cache_ready = False

        mocker.patch.object(cache, 'POPULATE_CACHE_BUDGET_SECONDS', 0.5)
        mocker.patch.object(
            cache, 'get_blender_releases',
            side_effect=lambda: time.sleep(30),
        )

        cache.populate_cache()

        assert cache._cache_ready is True
        assert cache._cached_series == ['4.5', '4.6']


# ---------------------------------------------------------------------
# #113 — every requests.get from the parser passes an explicit timeout.
# ---------------------------------------------------------------------

class TestParserAlwaysPassesTimeout:
    """Drift-guard: any future edit that drops the ``timeout=`` kwarg
    on a ``requests.get`` call in the parser chain re-introduces the
    hang. Asserting every call carries a non-None timeout catches that
    regression at test time."""

    def test_get_blender_releases_passes_timeout_on_every_call(
        self, mocker,
    ):
        index_html = (
            '<html><body>'
            '<a href="Blender4.5/">Blender4.5/</a>'
            '</body></html>'
        )
        sub_html = (
            '<html><body>'
            '<a href="blender-4.5.9-windows-x64.zip">x</a>'
            '<a href="blender-4.5.9.sha256">h</a>'
            '</body></html>'
        )

        class FakeResponse:
            def __init__(self, content):
                self.content = content

            def raise_for_status(self):
                pass

        captured_calls = []

        def fake_get(url, timeout=None):
            captured_calls.append({'url': url, 'timeout': timeout})
            if url.endswith('/release/'):
                return FakeResponse(index_html.encode())
            return FakeResponse(sub_html.encode())

        mocker.patch.object(
            parser.requests, 'get', side_effect=fake_get,
        )
        # hash_parser.get_all_hashes_from_url also calls requests.get;
        # stub the high-level function so we only assert the parser
        # module's own calls. The hash_parser timeout is covered by
        # the symmetrical constant in that module (see HTTP_TIMEOUT).
        mocker.patch.object(
            parser.hash_parser, 'get_all_hashes_from_url',
            return_value={},
        )

        parser.get_blender_releases()

        assert len(captured_calls) >= 2, (
            "Fake scrape didn't hit the parser — test setup is wrong. "
            f"Calls seen: {captured_calls}"
        )
        for call in captured_calls:
            assert call['timeout'] is not None, (
                f"requests.get({call['url']!r}) was invoked without a "
                "timeout kwarg. Issue #113 regression — every network "
                "call in the parser chain must carry an explicit "
                "timeout so a stalled socket can't hang the cache "
                "populate thread indefinitely."
            )

    def test_hash_parser_passes_timeout(self, mocker):
        """``hash_parser.get_all_hashes_from_url`` is the other network
        ingress used by the parser's sub-page scraper. Guard it
        independently so a refactor that drops its timeout doesn't
        slip by."""
        from workers.utils import hash_parser as hp

        captured = {}

        def fake_get(url, timeout=None):
            captured['timeout'] = timeout

            class R:
                text = ''

                def raise_for_status(self):
                    pass

            return R()

        mocker.patch.object(hp.requests, 'get', side_effect=fake_get)
        hp.get_all_hashes_from_url('https://example.test/x.sha256')
        assert captured.get('timeout') is not None, (
            "hash_parser.get_all_hashes_from_url must pass a non-None "
            "timeout to requests.get (issue #113)."
        )

    def test_resolve_latest_patch_default_timeout_is_not_none(self):
        """``resolve_latest_patch`` defaults its ``timeout`` kwarg to
        the module-level ``HTTP_TIMEOUT`` constant. That default must
        not silently become ``None`` — otherwise callers that rely on
        the default get an unbounded fetch. Guards against a refactor
        that accidentally drops the default."""
        import inspect
        sig = inspect.signature(parser.resolve_latest_patch)
        default = sig.parameters['timeout'].default
        assert default is not None
        assert default == parser.HTTP_TIMEOUT


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
