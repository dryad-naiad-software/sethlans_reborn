# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared Playwright helpers for Phase 2 wizard frontend integration tests.

These helpers drive the *real* wizard subprocess (spawned by the
``wizard_process`` fixture in :mod:`tests.integration.frontend.conftest`)
through the same browser flows an operator would see. The wizard now
binds plain HTTP on loopback (issue #170 — Caddy fronts TLS in prod;
the integration suite skips Caddy), so no ``ignore_https_errors``
gymnastics are required.

Helpers in this file:

* :func:`enter_token` — paste a token into ``index.html``, click
  Continue, wait for the post-auth navigation to settle.
* :func:`wait_for_path` — poll the page URL until it matches.
* :func:`get_progress_json` — read the wizard's
  ``.setup_progress.json`` from the test's data dir.
* :func:`write_progress_json` — pre-populate checkpoints (the resume
  scenario writes the file directly bypassing the handlers).
* :func:`mock_endpoint` — install a Playwright route handler that
  returns canned JSON. Used to fake FFmpeg / verify / database error
  paths without touching real backends.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

# Imported lazily inside functions to keep the test-collection step
# fast on hosts without playwright (it is in requirements-dev.txt).


def enter_token(page, base_url: str, token: str, *, expect_url=None) -> None:
    """Paste *token* on ``/token``, click Continue, wait for navigation.

    *expect_url* may be a full URL or a path; if given, we wait until
    the page lands on that URL before returning. Defaults to ``/`` (the
    welcome page is the standard happy-path destination when no
    checkpoints exist).
    """
    page.goto(f"{base_url}/token")
    page.wait_for_selector("#setup-token")
    page.fill("#setup-token", token)
    # Petite-vue's `:disabled` reactivity needs a microtask after the
    # input event to drop the disabled attribute on the submit button.
    page.wait_for_function(
        "() => !document.querySelector('button[type=\"submit\"]').disabled",
    )
    page.click("button[type='submit']")
    target = expect_url if expect_url is not None else "/"
    if isinstance(target, str) and target.startswith("/"):
        target_url = f"{base_url}{target}"
    else:
        target_url = target
    page.wait_for_url(target_url, timeout=5000)
    # Issue #187 — wait for the destination page's load event before
    # returning so the caller's next ``page.goto`` does not race the
    # destination's still-pending mount-time scripts. Playwright sees
    # an in-flight navigation as "interrupting" the next goto.
    page.wait_for_load_state("load", timeout=5000)


def wait_for_path(page, base_url: str, path: str, *, timeout: int = 5000) -> None:
    """Wait until the browser URL is exactly ``base_url + path``."""
    page.wait_for_url(f"{base_url}{path}", timeout=timeout)


def get_progress_json(data_dir: Path) -> dict:
    """Return the parsed ``.setup_progress.json`` envelope, or {} if absent."""
    p = data_dir / ".setup_progress.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def write_progress_json(
    data_dir: Path,
    checkpoints: Iterable[str],
    *,
    topology: str | None = None,
) -> None:
    """Pre-populate ``.setup_progress.json`` for resume tests.

    The resume test scenario presupposes a prior session got far enough
    to record checkpoints. Writing the file directly is the simplest
    way to set that up without driving the wizard through every page —
    the resume-target endpoint reads this file and the topology file
    (when ``topology`` is given) to compute the next route.
    """
    payload = {
        "schema_version": 1,
        "topology": topology,
        "checkpoints": list(checkpoints),
    }
    (data_dir / ".setup_progress.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )
    if topology is not None:
        (data_dir / "topology.json").write_text(
            json.dumps({"topology": topology}), encoding="utf-8",
        )


def mock_endpoint(page, url_pattern: str, *, status: int, body: dict) -> None:
    """Route every request matching *url_pattern* to a canned JSON response."""
    serialised = json.dumps(body)

    def _handler(route):
        route.fulfill(
            status=status,
            content_type="application/json",
            body=serialised,
        )

    page.route(url_pattern, _handler)


def captured_requests(page, url_pattern: str) -> list:
    """Install a request listener; return a list that grows over time.

    Useful for asserting "endpoint X was NEVER called" or "endpoint Y
    was called before endpoint Z".
    """
    captured: list[dict] = []

    def _on_request(request):
        # Match by suffix containment; Playwright's request.url is full.
        if url_pattern in request.url:
            captured.append({
                "url": request.url,
                "method": request.method,
                "post_data": request.post_data,
            })

    page.on("request", _on_request)
    return captured


__all__ = [
    "enter_token",
    "wait_for_path",
    "get_progress_json",
    "write_progress_json",
    "mock_endpoint",
    "captured_requests",
]
