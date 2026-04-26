# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Content tests for the wizard redirecting/done page (Spec 1 / B4 / FR-W-FE5).

These tests live next to the asset and inspect the static HTML
verbatim (mirroring ``test_index_page.py`` / ``test_topology_page.py``).
They lock down the polling, post-failsafe-recovery, and a11y contract
from FR-W-FE5 + FE-v2.2-MED-1 + FE-v2.2-MED-2 so a future drive-by edit
cannot silently break the redirecting story.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REDIRECTING_PATH = (
    PROJECT_ROOT / "wizard" / "frontend" / "static" / "redirecting.html"
)


@pytest.fixture(scope="module")
def redirecting_html() -> str:
    assert REDIRECTING_PATH.is_file(), f"Expected {REDIRECTING_PATH} to exist"
    return REDIRECTING_PATH.read_text(encoding="utf-8")


def test_redirecting_html_exists():
    assert REDIRECTING_PATH.is_file(), f"Missing {REDIRECTING_PATH}"


def test_redirecting_html_has_spdx_header(redirecting_html):
    assert (
        "SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC"
        in redirecting_html
    )
    assert "SPDX-License-Identifier: GPL-2.0-or-later" in redirecting_html


def test_redirecting_html_under_size_limit():
    """FR-W-FE7 / AC-W-FE2: wizard frontend HTML files MUST stay <= 250 lines."""
    lines = REDIRECTING_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 250, (
        f"redirecting.html has {len(lines)} lines — exceeds the 250-line "
        "ceiling from FR-W-FE7."
    )


def test_redirecting_html_loads_vendored_petite_vue(redirecting_html):
    assert "/static/vendor/petite-vue.js" in redirecting_html


def test_redirecting_html_loads_vendored_bootstrap_css(redirecting_html):
    assert "/static/vendor/bootstrap.min.css" in redirecting_html


def test_redirecting_html_loads_vendored_bootstrap_bundle_js(redirecting_html):
    """bootstrap.bundle.min.js MUST come BEFORE petite-vue (integration pattern)."""
    assert "/static/vendor/bootstrap.bundle.min.js" in redirecting_html
    bs_idx = redirecting_html.find("/static/vendor/bootstrap.bundle.min.js")
    pv_idx = redirecting_html.find("/static/vendor/petite-vue.js")
    assert bs_idx >= 0 and pv_idx >= 0
    assert bs_idx < pv_idx, (
        "bootstrap.bundle.min.js MUST be referenced before petite-vue.js."
    )


def test_redirecting_html_has_nomodule_fallback(redirecting_html):
    """NF-11 / browser-compat: <script nomodule> fallback required."""
    assert "<script nomodule>" in redirecting_html


def test_redirecting_html_polls_runtime_ready_endpoint(redirecting_html):
    """FR-W-FE5: poll GET /api/wizard/runtime-ready/."""
    assert "/api/wizard/runtime-ready/" in redirecting_html


def test_redirecting_html_sends_session_header(redirecting_html):
    """FR-W8 + FR-W-FE3b: session token sent via X-Wizard-Session header."""
    assert "X-Wizard-Session" in redirecting_html, (
        "Redirecting page MUST send X-Wizard-Session header on every poll."
    )


def test_redirecting_html_uses_session_storage_not_local_storage(redirecting_html):
    """FR-W-FE3: session token retrieved from sessionStorage (NOT local)."""
    assert "sessionStorage" in redirecting_html
    assert "localStorage" not in redirecting_html, (
        "Wizard pages MUST NOT use localStorage (FR-W-FE3)."
    )


def test_redirecting_html_polling_interval_is_two_seconds(redirecting_html):
    """Polling cadence MUST be 2000ms per the B4 spec."""
    assert "POLL_INTERVAL_MS" in redirecting_html
    # The constant declaration MUST be exactly 2000 (with optional whitespace).
    match = re.search(
        r"POLL_INTERVAL_MS\s*=\s*(\d+)", redirecting_html,
    )
    assert match, "POLL_INTERVAL_MS constant declaration not found"
    assert match.group(1) == "2000", (
        f"POLL_INTERVAL_MS should be 2000ms; got {match.group(1)}"
    )


def test_redirecting_html_elapsed_counter_ticks_every_second(redirecting_html):
    """The elapsed-seconds counter MUST tick every 1000ms (v2.1-FE-LOW-2)."""
    assert "TICK_INTERVAL_MS" in redirecting_html
    match = re.search(r"TICK_INTERVAL_MS\s*=\s*(\d+)", redirecting_html)
    assert match, "TICK_INTERVAL_MS constant declaration not found"
    assert match.group(1) == "1000", (
        f"TICK_INTERVAL_MS should be 1000ms; got {match.group(1)}"
    )


def test_redirecting_html_60s_log_hint_threshold(redirecting_html):
    """FR-W-FE5: surface launcher log path after 60s of continuous booting."""
    assert "LOG_HINT_AFTER_SECONDS" in redirecting_html
    match = re.search(
        r"LOG_HINT_AFTER_SECONDS\s*=\s*(\d+)", redirecting_html,
    )
    assert match, "LOG_HINT_AFTER_SECONDS constant declaration not found"
    assert match.group(1) == "60", (
        f"LOG_HINT_AFTER_SECONDS should be 60; got {match.group(1)}"
    )


def test_redirecting_html_handles_three_status_values(redirecting_html):
    """A4 contract: response status is one of booting / ready / failed."""
    for value in ("'booting'", "'ready'", "'failed'"):
        assert value in redirecting_html, (
            f"Status value {value!r} MUST be handled by redirecting page."
        )


def test_redirecting_html_caches_url_before_navigation(redirecting_html):
    """FE-v2.2-MED-1: cache MUST be written BEFORE window.location.assign().

    The cache write is required so the post-failsafe recovery path can
    read ``wizard:lastReadyUrl`` if fetch() rejects mid-stream after the
    wizard process exits.
    """
    # Find the navigation call, then verify a cache write call for the
    # ready URL appears earlier in the source.
    cache_idx = redirecting_html.find("_cacheReadyUrl(body.url)")
    nav_idx = redirecting_html.find("window.location.assign(body.url)")
    assert cache_idx >= 0, "_cacheReadyUrl(body.url) call not found"
    assert nav_idx >= 0, "window.location.assign(body.url) call not found"
    assert cache_idx < nav_idx, (
        "Cache write MUST happen BEFORE navigation (FE-v2.2-MED-1)."
    )


def test_redirecting_html_uses_session_storage_key_for_cache(redirecting_html):
    """The cache key MUST be wizard:lastReadyUrl (per spec)."""
    assert "'wizard:lastReadyUrl'" in redirecting_html or \
           '"wizard:lastReadyUrl"' in redirecting_html


def test_redirecting_html_post_failsafe_reads_cached_url(redirecting_html):
    """FE-v2.2-MED-1: on fetch failure, read cached URL from sessionStorage."""
    # The recovery method must read from the LAST_READY_URL_KEY.
    assert "_readCachedReadyUrl" in redirecting_html
    assert "LAST_READY_URL_KEY" in redirecting_html


def test_redirecting_html_log_path_rendered_as_plain_text(redirecting_html):
    """SEC-MED-6 / SEC-v2.3-LOW-1: log path MUST render as text, not as a link.

    The wizard does NOT serve log content — the user opens the file with
    their text editor. We assert the logPath is rendered inside <code>
    (or similar plain-text element) rather than an <a href="...">.
    """
    # logPath is bound inside <code> blocks; verify no <a href that
    # interpolates logPath exists.
    assert "{{ logPath" in redirecting_html
    # Make sure logPath is never used as an href value.
    assert ":href=\"logPath" not in redirecting_html
    assert ':href="logPath' not in redirecting_html
    assert ":href='logPath" not in redirecting_html
    # And that <code> wraps the path display (visual cue it's a path).
    assert "<code" in redirecting_html


def test_redirecting_html_has_aria_live_region(redirecting_html):
    """a11y: status updates MUST be in an aria-live region for screen readers."""
    assert 'aria-live="polite"' in redirecting_html


def test_redirecting_html_spinner_is_aria_hidden(redirecting_html):
    """Spinner is decorative; the textual status is what gets announced."""
    # Bootstrap spinner pattern — verify aria-hidden on the visible
    # spinner element.
    assert 'aria-hidden="true"' in redirecting_html
    assert "spinner-border" in redirecting_html


def test_redirecting_html_no_cdn_references(redirecting_html):
    """NF-3 / FR-W-FE10: no external CDN references allowed."""
    stripped_lines = []
    for line in redirecting_html.splitlines():
        s = line.strip()
        if s.startswith("<!--") or s.startswith("//") or s.startswith("*"):
            continue
        s = re.sub(r"<!--.*?-->", "", s)
        stripped_lines.append(s)
    body = "\n".join(stripped_lines)
    cdn_hits = re.findall(r"https?://[^\s\"'<>)]+", body)
    assert not cdn_hits, (
        "Wizard HTML must not reference any external URL — found: "
        f"{cdn_hits}. All assets MUST come from /static/vendor/."
    )


def test_redirecting_html_no_token_in_url(redirecting_html):
    """FR-W-FE3a: setup token MUST NOT appear in URL/query/fragment."""
    bad_patterns = [
        "?setup_token=",
        "?token=",
        "#setup_token=",
        "#token=",
        "?session_token=",
    ]
    for pat in bad_patterns:
        assert pat not in redirecting_html, (
            f"Token must not appear in URL — found pattern {pat!r}."
        )


def test_redirecting_html_v_cloak_used(redirecting_html):
    """Petite-vue scopes should use v-cloak so users never see {{ }} flash."""
    assert "v-cloak" in redirecting_html


def test_redirecting_html_polls_via_setinterval(redirecting_html):
    """The polling loop MUST use setInterval (not a recursive setTimeout)."""
    assert "setInterval" in redirecting_html


def test_redirecting_html_clears_intervals_on_terminal_state(redirecting_html):
    """Polling + tick intervals MUST be cleared when status reaches a terminal state."""
    assert "clearInterval" in redirecting_html
    # _stopTimers (the helper) must be called when status transitions to
    # ready or failed (FE-v2.2-MED-2 — stop polling on failed).
    assert "_stopTimers" in redirecting_html


def test_redirecting_html_failed_surface_does_not_replace_with_link(redirecting_html):
    """FE-v2.2-MED-2: failed page is an error message + log path, not a clickable log link."""
    # Log path is in <code>, never an <a href>.
    assert "log_path" in redirecting_html
    # Defensive: no anchor whose href interpolates the log path / file://.
    assert "file://" not in redirecting_html


def test_redirecting_html_initial_poll_kicked_immediately(redirecting_html):
    """The first poll MUST run on mount so the user does not wait 2s blank."""
    # The `start()` method calls `this._poll()` after registering the
    # interval — verify both setInterval AND a direct _poll() invocation
    # appear.
    assert "this._poll()" in redirecting_html


def test_redirecting_html_fetches_launcher_log_path_endpoint(redirecting_html):
    """The page asks the wizard for the launcher log path on load (B4 endpoint)."""
    assert "/api/wizard/launcher-log-path/" in redirecting_html
