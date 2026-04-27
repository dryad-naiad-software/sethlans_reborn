# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""JS-behaviour tests for ``redirecting.js`` (Spec 1 / B4 / FR-W-FE5).

Polls, intervals, post-failsafe recovery, transient-failure tolerance,
and double-navigation guards. Markup / structure assertions live in
``test_redirecting_page_markup.py`` so each file stays under the
300-line limit.
"""

from __future__ import annotations

import re

# The asset-loading fixtures (redirecting_html, redirecting_js,
# common_js) are provided by tests/unit/wizard/conftest.py.


def test_redirecting_html_polls_runtime_ready_endpoint(redirecting_js):
    """FR-W-FE5: poll GET /api/wizard/runtime-ready/."""
    assert "/api/wizard/runtime-ready/" in redirecting_js


def test_redirecting_html_sends_session_header(common_js):
    """FR-W8 + FR-W-FE3b: session token sent via X-Wizard-Session header.

    The header is added by the shared wizardFetch() helper in common.js.
    """
    assert "X-Wizard-Session" in common_js, (
        "common.js wizardFetch() MUST attach X-Wizard-Session on every poll."
    )


def test_redirecting_html_uses_session_storage_not_local_storage(
    common_js, redirecting_js,
):
    """FR-W-FE3: session token retrieved from sessionStorage (NOT local)."""
    assert "sessionStorage" in common_js
    assert "localStorage" not in common_js, (
        "Wizard pages MUST NOT use localStorage (FR-W-FE3)."
    )
    assert "localStorage" not in redirecting_js


def test_redirecting_html_polling_interval_is_two_seconds(redirecting_js):
    """Polling cadence MUST be 2000ms per the B4 spec."""
    assert "POLL_INTERVAL_MS" in redirecting_js
    # The constant declaration MUST be exactly 2000 (with optional whitespace).
    match = re.search(
        r"POLL_INTERVAL_MS\s*=\s*(\d+)", redirecting_js,
    )
    assert match, "POLL_INTERVAL_MS constant declaration not found"
    assert match.group(1) == "2000", (
        f"POLL_INTERVAL_MS should be 2000ms; got {match.group(1)}"
    )


def test_redirecting_html_elapsed_counter_ticks_every_second(redirecting_js):
    """The elapsed-seconds counter MUST tick every 1000ms (v2.1-FE-LOW-2)."""
    assert "TICK_INTERVAL_MS" in redirecting_js
    match = re.search(r"TICK_INTERVAL_MS\s*=\s*(\d+)", redirecting_js)
    assert match, "TICK_INTERVAL_MS constant declaration not found"
    assert match.group(1) == "1000", (
        f"TICK_INTERVAL_MS should be 1000ms; got {match.group(1)}"
    )


def test_redirecting_html_60s_log_hint_threshold(redirecting_js):
    """FR-W-FE5: surface launcher log path after 60s of continuous booting."""
    assert "LOG_HINT_AFTER_SECONDS" in redirecting_js
    match = re.search(
        r"LOG_HINT_AFTER_SECONDS\s*=\s*(\d+)", redirecting_js,
    )
    assert match, "LOG_HINT_AFTER_SECONDS constant declaration not found"
    assert match.group(1) == "60", (
        f"LOG_HINT_AFTER_SECONDS should be 60; got {match.group(1)}"
    )


def test_redirecting_html_handles_three_status_values(redirecting_js):
    """A4 contract: response status is one of booting / ready / failed."""
    for value in ("'booting'", "'ready'", "'failed'"):
        assert value in redirecting_js, (
            f"Status value {value!r} MUST be handled by redirecting.js."
        )


def test_redirecting_html_caches_url_before_navigation(redirecting_js):
    """FE-v2.2-MED-1: cache MUST be written BEFORE window.location.assign().

    The cache write is required so the post-failsafe recovery path can
    read ``wizard:lastReadyUrl`` if fetch() rejects mid-stream after the
    wizard process exits.
    """
    cache_idx = redirecting_js.find("cacheReadyUrl(body.url)")
    nav_idx = redirecting_js.find("window.location.assign(body.url)")
    assert cache_idx >= 0, "cacheReadyUrl(body.url) call not found"
    assert nav_idx >= 0, "window.location.assign(body.url) call not found"
    assert cache_idx < nav_idx, (
        "Cache write MUST happen BEFORE navigation (FE-v2.2-MED-1)."
    )


def test_redirecting_html_uses_session_storage_key_for_cache(common_js):
    """The cache key MUST be wizard:lastReadyUrl (per spec)."""
    assert "'wizard:lastReadyUrl'" in common_js or \
           '"wizard:lastReadyUrl"' in common_js


def test_redirecting_html_post_failsafe_reads_cached_url(
    redirecting_js, common_js,
):
    """FE-v2.2-MED-1: on fetch failure, read cached URL from sessionStorage."""
    # The recovery method must read from the LAST_READY_URL_KEY.
    assert "getCachedReadyUrl" in redirecting_js
    assert "LAST_READY_URL_KEY" in common_js


def test_redirecting_html_polls_via_setinterval(redirecting_js):
    """The polling loop MUST use setInterval (not a recursive setTimeout)."""
    assert "setInterval" in redirecting_js


def test_redirecting_html_clears_intervals_on_terminal_state(redirecting_js):
    """Polling + tick intervals MUST be cleared when status reaches a terminal state."""
    assert "clearInterval" in redirecting_js
    # _stopTimers (the helper) must be called when status transitions to
    # ready or failed (FE-v2.2-MED-2 — stop polling on failed).
    assert "_stopTimers" in redirecting_js


def test_redirecting_html_failed_surface_does_not_replace_with_link(
    redirecting_html, redirecting_js,
):
    """FE-v2.2-MED-2: failed page is an error message + log path, not a clickable log link."""
    # Log path is in <code>, never an <a href>.
    assert "log_path" in redirecting_js
    # Defensive: no anchor whose href interpolates the log path / file://.
    assert "file://" not in redirecting_html
    assert "file://" not in redirecting_js


def test_redirecting_html_initial_poll_kicked_immediately(redirecting_js):
    """The first poll MUST run on mount so the user does not wait 2s blank."""
    # The `start()` method calls `this._poll()` after registering the
    # interval — verify both setInterval AND a direct _poll() invocation
    # appear.
    assert "this._poll()" in redirecting_js


def test_redirecting_html_fetches_launcher_log_path_endpoint(redirecting_js):
    """The page asks the wizard for the launcher log path on load (B4 endpoint)."""
    assert "/api/wizard/launcher-log-path/" in redirecting_js


def test_redirecting_html_tolerates_transient_failures(redirecting_js):
    """MEDIUM-1 (Phase F2): a single transient 5xx must NOT trip post-failsafe.

    The page tolerates up to (FAILURE_TOLERANCE - 1) consecutive
    non-2xx responses before giving up. fetch() rejection still trips
    post-failsafe immediately — that is a genuine wizard-process-gone
    signal.
    """
    assert "FAILURE_TOLERANCE" in redirecting_js
    assert "_consecutiveFailures" in redirecting_js


def test_redirecting_js_uses_wall_clock_elapsed_counter(redirecting_js):
    """MEDIUM-5 (Phase F2): elapsed counter MUST be wall-clock based."""
    assert "_startedAt" in redirecting_js
    assert "Date.now()" in redirecting_js


def test_redirecting_js_validates_runtime_url_scheme(common_js):
    """LOW-5 (Phase F2): runtime URL MUST be scheme-validated before honour."""
    assert "isSafeRuntimeUrl" in common_js, (
        "common.js MUST expose isSafeRuntimeUrl for both cacheReadyUrl "
        "and the navigation guard (LOW-5)."
    )


def test_redirecting_js_guards_against_double_navigation(redirecting_js):
    """LOW-4 (Phase F2): a late poll after post-failsafe must not re-navigate."""
    assert "if (this.postFailsafeMode) return" in redirecting_js, (
        "redirecting.js MUST guard the ready branch against a poll that "
        "fires after _stopTimers() (LOW-4)."
    )
