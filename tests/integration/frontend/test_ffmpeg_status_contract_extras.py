# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Extended contract tests for ``GET /api/ffmpeg-status/``:

* Per-field type alignment between TS ``FFmpegDetails`` and DRF JSON.
* ``system-status.models.ts`` cross-validation (the system-status
  feature owns its own copy of ``FFmpegDetails`` per spec FR §102).
* Worker-token contract — ``TokenAuthentication`` accepted alongside
  ``SessionAuthentication`` (spec FR §84).

Companion to ``test_ffmpeg_status_contract.py`` — split to keep each
file under the 300-line Python limit.
"""

from __future__ import annotations

import pytest

from workers.services.parts_check import registry

from ._ffmpeg_contract_helpers import (
    EXPECTED_DETAILS_KEYS,
    EXPECTED_REGULAR_KEYS,
    FFMPEG_SERVICE_TS,
    SYSTEM_STATUS_MODELS_TS,
    URL,
    parse_ts_interface,
    seed_ffmpeg_status,
)


@pytest.fixture(autouse=True)
def scoped_parts_state():
    """Snapshot/restore FFmpeg status between tests."""
    prior = registry.get_status("ffmpeg")
    yield
    registry._publish("ffmpeg", prior)


@pytest.fixture
def ffmpeg_service_ts() -> str:
    return FFMPEG_SERVICE_TS.read_text(encoding="utf-8")


@pytest.fixture
def system_status_models_ts() -> str:
    return SYSTEM_STATUS_MODELS_TS.read_text(encoding="utf-8")


@pytest.mark.django_db
class TestFFmpegDetailsTypeAlignment:
    """Per-field type alignment between TS ``FFmpegDetails`` and DRF JSON."""

    def test_details_types_match_ts_declaration(
        self, admin_client, ffmpeg_service_ts,
    ):
        """For each field in TS ``FFmpegDetails``, DRF must emit a
        matching JSON-side primitive.

        TS declares:
            source: 'system' | 'bundled';
            version: string;
            path: string;
            status: 'ready' | 'installing' | 'failed';
            error: string | null;
        """
        seed_ffmpeg_status(
            status="ready", source="bundled", version="8.1",
            path="/data/bin/ffmpeg/8.1/ffmpeg",
        )
        resp = admin_client.get(URL)
        ffmpeg = resp.data["ffmpeg"]
        # Strings on every field except ``error`` (nullable string).
        for key in ("source", "version", "path", "status"):
            assert isinstance(ffmpeg[key], str), (
                f"{key!r} must be a str (TS expects ``string``); "
                f"got {type(ffmpeg[key]).__name__}"
            )
        assert ffmpeg["error"] is None or isinstance(ffmpeg["error"], str)

    def test_status_value_is_one_of_ts_union_literals(
        self, admin_client, ffmpeg_service_ts,
    ):
        """TS ``status: 'ready' | 'installing' | 'failed'`` — DRF must
        emit one of those three exact strings.  Drift here would trip
        TS exhaustive-switch checks at compile time."""
        for s in ("installing", "ready", "failed"):
            seed_ffmpeg_status(
                status=s,
                error="checksum_mismatch" if s == "failed" else None,
            )
            resp = admin_client.get(URL)
            assert resp.data["ffmpeg"]["status"] == s

    def test_failed_state_carries_closed_vocab_error_string(
        self, admin_client,
    ):
        """Spec FR §70-79 — ``error`` is a closed-vocabulary string,
        never raw exception text.  TS declares ``error: string | null``."""
        seed_ffmpeg_status(status="failed", error="extraction_unsafe")
        resp = admin_client.get(URL)
        assert resp.data["ffmpeg"]["error"] == "extraction_unsafe"
        assert isinstance(resp.data["ffmpeg"]["error"], str)
        # Closed-vocab string is short; never contains a path, traceback,
        # or stderr (defense-in-depth — spec FR §80).
        assert "\n" not in resp.data["ffmpeg"]["error"]
        assert len(resp.data["ffmpeg"]["error"]) < 64


@pytest.mark.django_db
class TestSystemStatusModelsAlignment:
    """``system-status.models.ts`` re-declares ``FFmpegDetails``.

    Spec FR §102 — the system-status feature owns its own copy of the
    ``FFmpegDetails`` interface so it does not depend on the core
    service file directly.  Both copies MUST expose the same fields,
    or the system-status page will silently mis-render when one drifts.
    """

    def test_two_ts_copies_of_ffmpeg_details_match(
        self, ffmpeg_service_ts, system_status_models_ts,
    ):
        service_copy = parse_ts_interface(
            ffmpeg_service_ts, "FFmpegDetails",
        )
        models_copy = parse_ts_interface(
            system_status_models_ts, "FFmpegDetails",
        )
        assert set(service_copy.keys()) == set(models_copy.keys()), (
            f"FFmpegDetails diverged between core service "
            f"({set(service_copy.keys())}) and system-status models "
            f"({set(models_copy.keys())})"
        )
        # And the type strings must be identical literal-for-literal —
        # the system-status component pattern-matches on the union
        # literals in its ``statusColor`` switch.
        for key in service_copy:
            assert service_copy[key] == models_copy[key], (
                f"FFmpegDetails.{key} type drifted between TS copies: "
                f"service={service_copy[key]!r} "
                f"models={models_copy[key]!r}"
            )

    def test_part_interface_uses_ffmpeg_details(
        self, system_status_models_ts,
    ):
        """``Part.details: FFmpegDetails`` — verify the link so a
        future field rename in ``FFmpegDetails`` cannot silently
        de-couple the page from the API shape."""
        fields = parse_ts_interface(
            system_status_models_ts, "Part",
        )
        assert "details" in fields
        assert "FFmpegDetails" in fields["details"], (
            f"Part.details type drifted: {fields['details']!r}"
        )

    def test_admin_response_satisfies_part_details_shape(
        self, admin_client,
    ):
        """End-to-end: an admin GET response's ``ffmpeg`` block can
        be assigned directly to ``Part.details`` (every required
        field is present with the right type)."""
        seed_ffmpeg_status(
            status="ready", source="bundled", version="8.1",
            path="/data/bin/ffmpeg/8.1/ffmpeg",
        )
        resp = admin_client.get(URL)
        ffmpeg = resp.data["ffmpeg"]
        # Mirrors the TS-side cast `parts.push({ name: 'FFmpeg',
        # details: resp.ffmpeg })` in system-status.component.ts.
        for key in EXPECTED_DETAILS_KEYS:
            assert key in ffmpeg, (
                f"Admin payload missing TS-required field {key!r}"
            )


@pytest.mark.django_db
class TestWorkerTokenContract:
    """Spec FR §84 — TokenAuthentication is supported alongside
    SessionAuthentication.  The frontend admin page uses the session
    cookie, but the worker agent sends a ``Token`` header.  Verify
    both auth paths produce a contract-conforming response shape."""

    def test_token_auth_returns_regular_payload_shape(
        self, worker_with_token,
    ):
        seed_ffmpeg_status(status="ready", source="system", version="8.1")
        _, client = worker_with_token
        resp = client.get(URL)
        assert resp.status_code == 200
        # Worker user is_staff=False → minimal payload, just like the
        # session-authed regular user.  TS DTO is the same union.
        assert set(resp.data.keys()) == EXPECTED_REGULAR_KEYS
        assert isinstance(resp.data["video_assembly_ready"], bool)
