# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Drift guard for the duplicated ``blender_release_parser`` module.

The manager and worker each ship a copy of the parser (per the
module-level note in both files). Issue #98 was fixed in both. This
test asserts the worker copy exposes the same post-fix contract: a
dict keyed by full version string that retains every patch the scrape
saw, not just the latest per series.

A deeper set of parser tests lives in
``tests/unit/manager/test_blender_release_parser.py``; this file only
verifies that the worker copy has not drifted away from the manager's
fix.
"""

from __future__ import annotations

from sethlans_worker_agent.utils import blender_release_parser as parser


class _FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


def test_worker_parser_returns_all_patches_not_just_latest(mocker):
    """Issue #98 regression for the worker copy."""
    index_html = (
        '<html><body>'
        '<a href="Blender4.5/">Blender4.5/</a>'
        '</body></html>'
    )
    version_page = (
        '<html><body>'
        '<a href="blender-4.5.8-windows-x64.zip">4.5.8</a>'
        '<a href="blender-4.5.9-windows-x64.zip">4.5.9</a>'
        '<a href="blender-4.5.8.sha256">h</a>'
        '<a href="blender-4.5.9.sha256">h</a>'
        '</body></html>'
    )

    def fake_get(url, timeout=10):
        if url.endswith("/release/"):
            return _FakeResponse(index_html.encode())
        if url.endswith("Blender4.5/"):
            return _FakeResponse(version_page.encode())
        raise AssertionError(f"Unexpected URL: {url}")

    mocker.patch.object(parser.requests, "get", side_effect=fake_get)
    mocker.patch.object(
        parser.hash_parser, "get_all_hashes_from_url",
        return_value={
            "blender-4.5.8-windows-x64.zip": "aa" * 32,
            "blender-4.5.9-windows-x64.zip": "bb" * 32,
        },
    )

    result = parser.get_blender_releases()
    assert "4.5.8" in result, (
        "Worker parser drifted from manager fix (#98). Both copies "
        "must retain every patch; 4.5.8 should be findable even when "
        "4.5.9 exists."
    )
    assert "4.5.9" in result
