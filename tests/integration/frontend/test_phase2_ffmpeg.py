# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Phase 2 (Spec 2) FFmpeg page Playwright tests (FR-M2-7).

The real wizard handler downloads FFmpeg — we MUST NOT exercise that
in the test suite. Every test mocks ``/api/wizard/ffmpeg/start/`` and
``/api/wizard/ffmpeg/progress/<task_id>/`` via ``page.route`` so the
JS controller drives a deterministic state machine.
"""

from __future__ import annotations

import json

import pytest

from ._phase2_helpers import enter_token, write_progress_json


def _land_on_ffmpeg(page, wp):
    """Use the resume walker to land directly on /ffmpeg.

    The progress walker treats manager topology as auto-skipping
    worker_password_set, so a manager-topology pre-population takes us
    straight here without driving every page.
    """
    write_progress_json(
        wp.data_dir,
        [
            "welcome_seen",
            "topology_chosen",
            "network_configured",
            "database_configured",
            "admin_validated",
        ],
        topology="manager",
    )
    enter_token(page, wp.base_url, wp.setup_token, expect_url="/ffmpeg")


def _install_progress_mock(page, *, status_sequence):
    """Install a route handler that drains *status_sequence* in order.

    Each tick of the JS poll consumes the next entry; the last entry
    repeats forever so the page can settle.
    """
    seq = list(status_sequence)
    state = {"i": 0}

    def _start_handler(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"task_id": "ffmpeg-task-1", "status": "in_progress"}),
        )

    def _progress_handler(route):
        idx = min(state["i"], len(seq) - 1)
        body = dict(seq[idx])
        state["i"] += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(body),
        )

    page.route("**/api/wizard/ffmpeg/start/", _start_handler)
    page.route("**/api/wizard/ffmpeg/progress/**", _progress_handler)


def test_progress_bar_completes(page, wizard_process):
    wp = wizard_process
    _install_progress_mock(page, status_sequence=[
        {"status": "downloading", "percent": 25},
        {"status": "downloading", "percent": 75},
        {"status": "complete", "percent": 100},
    ])
    _land_on_ffmpeg(page, wp)
    page.wait_for_selector("#ffmpeg-next:not([disabled])", timeout=10000)
    page.wait_for_function(
        "() => document.querySelector('.progress-bar').style.width === '100%'",
        timeout=5000,
    )


def test_aria_busy_during_download(page, wizard_process):
    wp = wizard_process
    _install_progress_mock(page, status_sequence=[
        {"status": "downloading", "percent": 10},
        {"status": "downloading", "percent": 20},
        {"status": "downloading", "percent": 30},
    ])
    _land_on_ffmpeg(page, wp)
    page.wait_for_function(
        "() => {"
        "const el = document.querySelector('.ffmpeg-progress');"
        "return el && el.getAttribute('aria-busy') === 'true';"
        "}",
        timeout=5000,
    )


@pytest.mark.parametrize("category,headline_fragment,retry_allowed", [
    ("download_failed", "Could not download", True),
    ("sha_mismatch", "verification failed", False),
    ("version_mismatch", "unexpected version", False),
    ("extraction_failed", "could not be extracted", True),
    ("network_error", "Network error", True),
])
def test_failure_categories_render(
    page, wizard_process, category, headline_fragment, retry_allowed,
):
    wp = wizard_process
    _install_progress_mock(page, status_sequence=[
        {"status": "downloading", "percent": 10},
        {
            "status": "failed",
            "percent": 10,
            "category": category,
            "error": "underlying detail string",
        },
    ])
    _land_on_ffmpeg(page, wp)
    page.wait_for_selector(".alert-danger", state="visible", timeout=10000)
    text = page.locator(".alert-danger").inner_text()
    assert headline_fragment.lower() in text.lower(), (category, text)
    retry_btn_count = page.locator(".alert-danger button:has-text('Retry')").count()
    if retry_allowed:
        assert retry_btn_count == 1, (category, text)
    else:
        assert retry_btn_count == 0, (category, text)


def test_view_details_disclosure_contains_error(page, wizard_process):
    wp = wizard_process
    _install_progress_mock(page, status_sequence=[
        {
            "status": "failed",
            "percent": 10,
            "category": "download_failed",
            "error": "specific-backend-detail-XYZ",
        },
    ])
    _land_on_ffmpeg(page, wp)
    page.wait_for_selector("details summary", timeout=10000)
    page.click("details summary")
    page.wait_for_selector("details pre", state="visible", timeout=2000)
    pre_text = page.locator("details pre").inner_text()
    assert "specific-backend-detail-XYZ" in pre_text, pre_text


def test_complete_enables_continue_to_verify(page, wizard_process):
    wp = wizard_process
    _install_progress_mock(page, status_sequence=[
        {"status": "complete", "percent": 100},
    ])
    _land_on_ffmpeg(page, wp)
    page.wait_for_selector("#ffmpeg-next:not([disabled])", timeout=10000)
    page.click("#ffmpeg-next")
    page.wait_for_url(f"{wp.base_url}/verify", timeout=5000)


def test_form_state_cleared_on_complete(page, wizard_process):
    """FR-CHK3-FORM-STATE — the FFmpeg complete event clears all stash."""
    wp = wizard_process
    # Slow the mock so the page doesn't transition to complete before
    # we get a chance to seed sessionStorage.
    _install_progress_mock(page, status_sequence=[
        {"status": "downloading", "percent": 5},
        {"status": "downloading", "percent": 25},
        {"status": "downloading", "percent": 50},
        {"status": "downloading", "percent": 75},
        {"status": "complete", "percent": 100},
    ])
    _land_on_ffmpeg(page, wp)
    # Wait for the FFmpeg page DOM to fully render (.ffmpeg-progress is
    # the page's main panel). Once it's visible, navigation has settled
    # and the execution context is stable for evaluate().
    page.wait_for_selector(".ffmpeg-progress", state="visible", timeout=5000)
    page.wait_for_load_state("domcontentloaded")
    # Pre-populate a fake stash key BEFORE the completion clears it.
    page.evaluate(
        "() => window.sessionStorage.setItem("
        "'wizard.form.network', '{\"allowExternal\":true}')",
    )
    # Wait for completion + clearAllFormState to fire.
    page.wait_for_function(
        "() => window.sessionStorage.getItem('wizard.form.network') === null",
        timeout=10000,
    )
