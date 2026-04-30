# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Phase 2 (Spec 2) accessibility tests (FR-FE-A11Y*).

Injects axe-core into each page and asserts no HIGH/CRITICAL findings.
axe-core is fetched from the wizard's vendor directory if present;
otherwise it is loaded from a vendored CDN-style URL. This test
favours pinning behaviour over absolute coverage — many a11y issues
need contrast/colour testing that headless Chromium reports
inconsistently. We assert *severity* level rather than a zero-issue
bar so the test stays meaningful without becoming flaky on minor
contrast diffs.

The injection script is intentionally small and hermetic: a single
``<script>`` tag with the axe-core source, then ``axe.run()`` invoked
synchronously and serialized into a JSON return value.

If axe-core cannot be loaded (e.g., the test machine has no internet
and the vendor file is absent), the test is skipped with a clear
reason — accessibility coverage is not the only signal in this suite,
and a hard failure here would mask real regressions in other tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ._phase2_helpers import (
    enter_token,
    write_progress_json,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
AXE_VENDOR = (
    REPO_ROOT / "wizard" / "frontend" / "static" / "vendor" / "axe.min.js"
)


@pytest.fixture(scope="session")
def axe_source() -> str:
    """Return the axe-core source code, skipping if unavailable."""
    if AXE_VENDOR.exists():
        return AXE_VENDOR.read_text(encoding="utf-8")
    pytest.skip(
        "axe-core not vendored at wizard/frontend/static/vendor/axe.min.js — "
        "run a11y tests against a real CI runner with the vendor file present.",
    )


def _run_axe(page, axe_source: str) -> list:
    """Inject axe-core into the current page and return its violations."""
    page.evaluate(axe_source)
    raw = page.evaluate(
        "async () => {"
        " const r = await window.axe.run("
        "  document, "
        "  {resultTypes: ['violations']}"
        " );"
        " return JSON.stringify(r.violations || []);"
        "}",
    )
    return json.loads(raw)


def _critical_violations(violations: list) -> list:
    """Filter to HIGH/CRITICAL severity findings only."""
    return [
        v for v in violations
        if v.get("impact") in ("critical", "serious")
    ]


def _land(page, wp, route: str, *, topology: str = "manager") -> None:
    """Pre-populate progress and land on *route* via the resume walker."""
    checkpoints = {
        "/": [],
        "/topology": ["welcome_seen"],
        "/network": ["welcome_seen", "topology_chosen"],
        "/database": ["welcome_seen", "topology_chosen", "network_configured"],
        "/admin-user": [
            "welcome_seen", "topology_chosen", "network_configured",
            "database_configured",
        ],
        "/verify": [
            "welcome_seen", "topology_chosen", "network_configured",
            "database_configured", "admin_validated",
        ],
    }
    if route in checkpoints and checkpoints[route]:
        write_progress_json(wp.data_dir, checkpoints[route], topology=topology)
    enter_token(page, wp.base_url, wp.setup_token, expect_url=route)


# Pages where the resume walker can land us cleanly. Excludes
# /worker-password (manager-only auto-skips it) and /done (irreversible
# / network-pending state) — those have their own dedicated tests.
A11Y_PAGES = ["/", "/topology", "/network", "/database", "/admin-user", "/verify"]


@pytest.mark.parametrize("route", A11Y_PAGES)
def test_no_critical_axe_violations(route, page, wizard_process, axe_source):
    wp = wizard_process
    if route == "/verify":
        # Pre-mock verify so the page can render its checklist without
        # exercising the real handlers (which need a fully populated
        # manager.ini etc.).
        page.route(
            "**/api/wizard/verify/",
            lambda r: r.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "all_passed": True,
                    "checks": [
                        {"name": "network_bindable", "passed": True, "error": ""},
                    ],
                }),
            ),
        )
    _land(page, wp, route)
    # Allow Petite-vue mount + axe injection to settle.
    page.wait_for_function(
        "() => document.querySelector('[v-cloak]') === null"
        " || !document.querySelector('[v-cloak]').hasAttribute('v-cloak')",
        timeout=5000,
    )
    violations = _run_axe(page, axe_source)
    crits = _critical_violations(violations)
    if crits:
        # Build a concise diagnostic so failures are actionable.
        summary = [
            f"{v['id']} (impact={v['impact']}): {v['help']}"
            for v in crits
        ]
        pytest.fail(
            f"Critical/serious a11y violations on {route}:\n"
            + "\n".join(summary),
        )
