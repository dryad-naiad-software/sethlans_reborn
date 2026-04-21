# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Regression tests for ``workers.utils.blender_release_parser`` (issue #98).

The parser previously filtered its scrape of download.blender.org down
to one patch per major.minor series, which silently broke
``tool_manager.ensure_blender_version_available`` whenever the manager
DB pinned a specific older patch that was no longer "the latest"
(e.g. migration 0008 stores ``4.5: 4.5.8`` but Blender has since
released 4.5.9). Manager heartbeats told workers to install 4.5.8;
workers asked the parser for 4.5.8; parser had filtered 4.5.8 out and
only retained 4.5.9; download failed; jobs stayed QUEUED.

Fix: return every patch the scrape saw. Latest-per-series derivation
is still available to callers that want it, but via a separate helper
(``_log_latest_per_series``) and not baked into the return value.
"""

from __future__ import annotations

import pytest

from workers.utils import blender_release_parser as parser


class _FakeResponse:
    """Minimal stand-in for ``requests.Response`` used by the parser."""

    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


def _install_fake_network(mocker, index_html, version_pages, hashes):
    """Patch ``requests.get`` + ``parse_version_page`` for a deterministic scrape.

    * ``index_html`` — HTML returned for the top-level /release/ page.
    * ``version_pages`` — dict mapping ``Blender4.5/`` to HTML for that
      sub-page (anchors pointing at blender-X.Y.Z-<platform>.zip files).
    * ``hashes`` — dict mapping filename to its SHA-256; consumed by the
      hash-parser substitute.
    """
    def fake_get(url, timeout=10):
        if url.endswith("/release/"):
            return _FakeResponse(index_html.encode())
        for suffix, html in version_pages.items():
            if url.endswith(suffix):
                return _FakeResponse(html.encode())
        raise AssertionError(f"Unexpected URL in fake scrape: {url}")

    mocker.patch.object(parser.requests, "get", side_effect=fake_get)
    mocker.patch.object(
        parser.hash_parser, "get_all_hashes_from_url",
        side_effect=lambda sha_url: hashes,
    )


# ---------------------------------------------------------------------
# Regression #98 — every patch is kept; older patches are still findable.
# ---------------------------------------------------------------------

class TestGetBlenderReleasesKeepsEveryPatch:
    """The dict returned by ``get_blender_releases`` must contain every
    patch the scrape saw, not just the latest per series — otherwise the
    manager's pinned ``resolved_version`` can reference a patch the
    worker's parser claims doesn't exist (issue #98)."""

    def _scrape(self, mocker):
        index_html = (
            '<html><body>'
            '<a href="Blender4.5/">Blender4.5/</a>'
            '<a href="Blender3.6/">Blender3.6/</a>'  # Pre-4.0; filtered.
            '</body></html>'
        )
        version_pages = {
            "Blender4.5/": (
                '<html><body>'
                '<a href="blender-4.5.8-windows-x64.zip">4.5.8 win</a>'
                '<a href="blender-4.5.9-windows-x64.zip">4.5.9 win</a>'
                '<a href="blender-4.5.8-linux-x64.tar.xz">4.5.8 linux</a>'
                '<a href="blender-4.5.9-linux-x64.tar.xz">4.5.9 linux</a>'
                '<a href="blender-4.5.8.sha256">hash</a>'
                '<a href="blender-4.5.9.sha256">hash</a>'
                '</body></html>'
            ),
        }
        hashes = {
            "blender-4.5.8-windows-x64.zip": "aa" * 32,
            "blender-4.5.9-windows-x64.zip": "bb" * 32,
            "blender-4.5.8-linux-x64.tar.xz": "cc" * 32,
            "blender-4.5.9-linux-x64.tar.xz": "dd" * 32,
        }
        _install_fake_network(mocker, index_html, version_pages, hashes)
        return parser.get_blender_releases()

    def test_both_patches_present_in_result(self, mocker):
        result = self._scrape(mocker)
        assert "4.5.8" in result, (
            "Issue #98 regression: older patch was filtered out of the "
            "parser result; manager pins to specific patches, so every "
            "seen patch must survive."
        )
        assert "4.5.9" in result

    def test_result_keyed_by_full_version(self, mocker):
        """Keys must be ``X.Y.Z`` strings — downstream callers
        (tool_manager, blender_download) do ``releases.get(full_ver)``."""
        result = self._scrape(mocker)
        for key in result:
            parts = key.split(".")
            assert len(parts) == 3
            assert all(p.isdigit() for p in parts)

    def test_platform_payload_preserved(self, mocker):
        """Each patch carries its per-platform {url, sha256} blob."""
        result = self._scrape(mocker)
        entry = result["4.5.8"]
        assert "windows-x64" in entry
        assert entry["windows-x64"]["url"].endswith(
            "blender-4.5.8-windows-x64.zip"
        )
        assert entry["windows-x64"]["sha256"] == "aa" * 32
        assert "linux-x64" in entry

    def test_pre_4_major_versions_still_skipped(self, mocker):
        """3.x releases are intentionally excluded — they predate the
        cache-manifest contract the worker expects. Regression guard."""
        result = self._scrape(mocker)
        assert not any(key.startswith("3.") for key in result)

    def test_log_helper_derives_latest_per_series(self, mocker):
        """``_log_latest_per_series`` preserves the informational log
        behaviour the UI / operator output relied on, without mutating
        the return value. Asserted against ``logger.info`` directly
        because ``pytest.ini`` routes log_cli through a non-propagating
        handler that neither caplog nor capsys captures reliably."""
        info_mock = mocker.patch.object(parser.logger, "info")
        self._scrape(mocker)
        info_messages = [
            call.args[0] % call.args[1:] if len(call.args) > 1
            else call.args[0]
            for call in info_mock.call_args_list
        ]
        assert any(
            "Selected latest for 4.5 series: 4.5.9" in m
            for m in info_messages
        ), (
            "Operator-facing 'latest per series' log must survive the "
            "#98 refactor — it is how admins verify which patch the UI "
            "will surface as the default."
        )


# ---------------------------------------------------------------------
# Unchanged contract around network failure.
# ---------------------------------------------------------------------

class TestGetBlenderReleasesFailureModes:

    def test_index_fetch_failure_returns_empty_dict(self, mocker):
        """Top-level index unavailable → no releases known. Must not
        crash; the worker falls through to its local scan / cache."""
        mocker.patch.object(
            parser.requests, "get",
            side_effect=parser.requests.exceptions.ConnectionError(
                "no network"
            ),
        )
        result = parser.get_blender_releases()
        assert result == {}

    def test_sub_page_failure_does_not_abort_whole_scrape(
        self, mocker, caplog,
    ):
        """If a single version page fails to load, other series must
        still resolve. Prevents a single blender.org hiccup from wiping
        all version data."""
        index_html = (
            '<html><body>'
            '<a href="Blender4.5/">Blender4.5/</a>'
            '<a href="Blender5.0/">Blender5.0/</a>'
            '</body></html>'
        )

        def fake_get(url, timeout=10):
            if url.endswith("/release/"):
                return _FakeResponse(index_html.encode())
            if "Blender4.5" in url:
                raise parser.requests.exceptions.ConnectionError("4.5 down")
            if "Blender5.0" in url:
                return _FakeResponse(
                    b'<html><a href="blender-5.0.0-windows-x64.zip">x</a>'
                    b'<a href="blender-5.0.0.sha256">h</a></html>'
                )
            raise AssertionError(f"Unexpected URL: {url}")

        mocker.patch.object(parser.requests, "get", side_effect=fake_get)
        mocker.patch.object(
            parser.hash_parser, "get_all_hashes_from_url",
            return_value={"blender-5.0.0-windows-x64.zip": "ee" * 32},
        )
        result = parser.get_blender_releases()
        assert "5.0.0" in result
        # 4.5.x entries are absent — that's the correct degradation.
        assert not any(k.startswith("4.5") for k in result)


# ---------------------------------------------------------------------
# _log_latest_per_series behaviour in isolation.
# ---------------------------------------------------------------------

class TestLogLatestPerSeries:

    def test_picks_highest_patch_by_integer_sort(self, mocker):
        """Lexicographic sort would pick 4.5.9 > 4.5.10 incorrectly.
        The helper must sort patch numbers as ints."""
        info_mock = mocker.patch.object(parser.logger, "info")
        releases = {
            "4.5.9": {}, "4.5.10": {}, "4.5.8": {},
        }
        parser._log_latest_per_series(releases)
        messages = [call.args[0] for call in info_mock.call_args_list]
        assert any(
            "Selected latest for 4.5 series: 4.5.10" in m
            for m in messages
        ), (
            "Integer-aware sort regression: 4.5.10 must beat 4.5.9, "
            "not lose to it as a string comparison would."
        )

    def test_empty_dict_logs_nothing(self, mocker):
        info_mock = mocker.patch.object(parser.logger, "info")
        parser._log_latest_per_series({})
        info_mock.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
