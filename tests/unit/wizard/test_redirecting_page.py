# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Content tests for the wizard redirecting/done page (Spec 1 / B4 / FR-W-FE5).

These tests live next to the asset and inspect the static HTML
verbatim (mirroring ``test_index_page.py`` / ``test_topology_page.py``).
They lock down the polling, post-failsafe-recovery, and a11y contract
from FR-W-FE5 + FE-v2.2-MED-1 + FE-v2.2-MED-2 so a future drive-by edit
cannot silently break the redirecting story.

Phase F2 split the inline page script out into ``static/js/redirecting.js``.
Markup-shape tests still inspect ``redirecting.html`` directly; behaviour
tests now read the JS files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REDIRECTING_PATH = (
    PROJECT_ROOT / "wizard" / "frontend" / "static" / "redirecting.html"
)
REDIRECTING_JS_PATH = (
    PROJECT_ROOT / "wizard" / "frontend" / "static" / "js" / "redirecting.js"
)
COMMON_JS_PATH = (
    PROJECT_ROOT / "wizard" / "frontend" / "static" / "js" / "common.js"
)


@pytest.fixture(scope="module")
def redirecting_html() -> str:
    assert REDIRECTING_PATH.is_file(), f"Expected {REDIRECTING_PATH} to exist"
    return REDIRECTING_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def redirecting_js() -> str:
    assert REDIRECTING_JS_PATH.is_file(), (
        f"Expected {REDIRECTING_JS_PATH} to exist"
    )
    return REDIRECTING_JS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def common_js() -> str:
    assert COMMON_JS_PATH.is_file(), f"Expected {COMMON_JS_PATH} to exist"
    return COMMON_JS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def redirecting_combined(
    redirecting_html: str, redirecting_js: str, common_js: str,
) -> str:
    """Concatenated HTML + JS deps for cross-file behaviour assertions."""
    return "\n".join((redirecting_html, redirecting_js, common_js))


def test_redirecting_html_exists():
    assert REDIRECTING_PATH.is_file(), f"Missing {REDIRECTING_PATH}"


def test_redirecting_html_has_spdx_header(redirecting_html):
    assert (
        "SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC"
        in redirecting_html
    )
    assert "SPDX-License-Identifier: GPL-2.0-or-later" in redirecting_html


def test_redirecting_js_has_spdx_header(redirecting_js):
    assert (
        "SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC"
        in redirecting_js
    )
    assert "SPDX-License-Identifier: GPL-2.0-or-later" in redirecting_js


def test_redirecting_html_under_size_limit():
    """FR-W-FE7 / AC-W-FE2: wizard frontend HTML files MUST stay <= 250 lines."""
    lines = REDIRECTING_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 250, (
        f"redirecting.html has {len(lines)} lines — exceeds the 250-line "
        "ceiling from FR-W-FE7."
    )


def test_redirecting_js_under_size_limit():
    """FR-W-FE7 also applies to the per-page JS file (Phase F2)."""
    lines = REDIRECTING_JS_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 250, (
        f"redirecting.js has {len(lines)} lines — exceeds the 250-line "
        "ceiling."
    )


def test_redirecting_html_loads_vendored_petite_vue(redirecting_js):
    assert "/static/vendor/petite-vue.js" in redirecting_js


def test_redirecting_html_loads_vendored_bootstrap_css(redirecting_html):
    assert "/static/vendor/bootstrap.min.css" in redirecting_html


def test_redirecting_html_loads_vendored_bootstrap_bundle_js(redirecting_html):
    """bootstrap.bundle.min.js MUST come BEFORE the per-page module script."""
    assert "/static/vendor/bootstrap.bundle.min.js" in redirecting_html
    bs_idx = redirecting_html.find("/static/vendor/bootstrap.bundle.min.js")
    js_idx = redirecting_html.find("/static/js/redirecting.js")
    assert bs_idx >= 0 and js_idx >= 0
    assert bs_idx < js_idx, (
        "bootstrap.bundle.min.js MUST be referenced before redirecting.js."
    )


def test_redirecting_html_loads_extracted_page_script(redirecting_html):
    """redirecting.html MUST reference the extracted redirecting.js module."""
    assert "/static/js/redirecting.js" in redirecting_html
    assert 'type="module"' in redirecting_html


def test_redirecting_html_has_nomodule_fallback(redirecting_html):
    """NF-11 / browser-compat: <script nomodule> fallback required."""
    assert "<script nomodule>" in redirecting_html


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
    """a11y: status updates MUST be in an aria-live region for screen readers.

    Phase F2 (MEDIUM-4) added a binding so the inner region escalates
    to assertive when status === 'failed'. The :aria-live binding (a
    Petite-vue attribute bind) MUST appear, and a polite literal MUST
    still exist on at least one banner.
    """
    assert ':aria-live="statusLiveRegionPoliteness"' in redirecting_html
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


def test_redirecting_html_no_token_in_url(redirecting_combined):
    """FR-W-FE3a: setup token MUST NOT appear in URL/query/fragment."""
    bad_patterns = [
        "?setup_token=",
        "?token=",
        "#setup_token=",
        "#token=",
        "?session_token=",
    ]
    for pat in bad_patterns:
        assert pat not in redirecting_combined, (
            f"Token must not appear in URL — found pattern {pat!r}."
        )


def test_redirecting_html_v_cloak_used(redirecting_html):
    """Petite-vue scopes should use v-cloak so users never see {{ }} flash."""
    assert "v-cloak" in redirecting_html


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


def test_redirecting_html_renders_tls_warning_banner(redirecting_html):
    """HIGH-2 (Phase F2): the TLS warning MUST appear above the spinner.

    Without the banner the user lands on the runtime URL, sees the
    self-signed cert prompt, and has no context for what's happening.
    The warning text covers FR-W-FE5's mandate.
    """
    lower = redirecting_html.lower()
    assert "security warning" in lower, (
        "redirecting.html MUST surface the FR-W-FE5 TLS warning above "
        "the spinner so the user knows the cert prompt is expected."
    )
    # The banner becomes a clickable link to the runtime URL when ready.
    assert ':href="runtimeUrl"' in redirecting_html, (
        "When status === 'ready' the runtime URL MUST be rendered as a "
        "clickable fallback link inside the banner (HIGH-2)."
    )


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
