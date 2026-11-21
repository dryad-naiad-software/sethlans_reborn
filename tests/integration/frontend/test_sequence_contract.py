# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frontend-backend contract test for the Unreal sequence UI (sequence
rendering spec, chunk 8).

The shapes in ``core/services/sequence.types.ts`` are checked against real
API answers (``_ts_shape_checker``): the discovery list and detail, the
submission list and detail, and the ``downloads`` flags of a version and
of its shots.  The data is made by the real flow (the chunk 3 and 6
helpers): a discovery read by a worker, a submission planned from it,
every piece rendered but the last, which fails, and the shot videos built
with a fake FFmpeg, so the version ends PARTLY_DONE with two DONE shots
(video built) and one ERROR shot.  The status unions and the final
statuses of the TS file are checked against the model choices.
"""

from __future__ import annotations

import re
from unittest import mock

import pytest

from workers.constants_sequence import FINAL_SEQUENCE_STATUSES, DiscoveryStatus, SequenceStatus, ShotStatus
from workers.constants_video import VideoStatus

from tests.integration.manager.sequence.build_helpers import (
    RUN_FFMPEG, FakeFfmpeg, build_all, finish_pieces, manual_queue, version,
)
from tests.integration.manager.sequence.submission_helpers import SUBMISSIONS, VIDEO
from tests.integration.manager.unreal_helpers import unreal_asset

from ._ffmpeg_contract_helpers import FRONTEND_ROOT
from ._ts_shape_checker import TsShapes

TYPES_TS = FRONTEND_ROOT / "core" / "services" / "sequence.types.ts"
DISCOVERIES = "/api/sequence-discoveries/"
NOTHING = {"video": False, "video_sound": False, "sound": False, "frames": False}

pytestmark = pytest.mark.django_db


@pytest.fixture(scope="module")
def ts() -> TsShapes:
    return TsShapes(TYPES_TS.read_text(encoding="utf-8"))


@pytest.fixture
def unreal(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / "media")
    return unreal_asset()


@pytest.fixture
def partly_done(admin_client, unreal, worker_with_token, django_capture_on_commit_callbacks):
    """A PARTLY_DONE version: shots 1 and 2 DONE with their videos, shot 3 ERROR."""
    worker, client = worker_with_token
    submission = version(admin_client, unreal, worker, client, video_settings=VIDEO, sound=False)
    finish_pieces(client, worker, submission, fail_last=True)
    with manual_queue(), mock.patch(RUN_FFMPEG, FakeFfmpeg()):
        build_all(django_capture_on_commit_callbacks, submission.pk)
    submission.refresh_from_db()
    assert submission.status == SequenceStatus.PARTLY_DONE
    return submission


def _get(client, url, **params):
    resp = client.get(url, params)
    assert resp.status_code == 200, resp.content
    return resp.json()


def _union(ts: TsShapes, alias: str) -> set:
    return {None if m == "null" else m.strip("'") for m in ts.aliases[alias].split(" | ")}


class TestStatusUnions:
    """The TS status unions name exactly the model's choices."""

    @pytest.mark.parametrize("alias, choices", [
        ("SequenceStatus", SequenceStatus), ("ShotStatus", ShotStatus), ("DiscoveryStatus", DiscoveryStatus),
    ])
    def test_status_union_matches_the_model(self, ts, alias, choices):
        assert _union(ts, alias) == set(choices.values)

    def test_item_status_is_the_video_status_or_null(self, ts):
        assert _union(ts, "ItemStatus") == set(VideoStatus.values) | {None}

    def test_final_statuses_match_the_manager(self):
        source = TYPES_TS.read_text(encoding="utf-8")
        match = re.search(r"FINAL_SEQUENCE_STATUSES[^=]*=\s*new Set<SequenceStatus>\(\[([^\]]*)\]\)", source)
        assert match, "FINAL_SEQUENCE_STATUSES not found"
        assert set(re.findall(r"'(\w+)'", match.group(1))) == set(FINAL_SEQUENCE_STATUSES)

    def test_every_status_has_a_label(self):
        source = TYPES_TS.read_text(encoding="utf-8")
        labels = re.search(r"SEQUENCE_STATUS_LABELS[^{]*\{([^}]*)\}", source).group(1)
        assert set(re.findall(r"(\w+):", labels)) == set(SequenceStatus.values)


class TestSubmissionShapes:
    """``Page<SequenceSubmission>``, ``SequenceSubmission``, ``SequenceShot`` and ``Downloads``."""

    def test_list_page_matches_ts(self, admin_client, ts, partly_done):
        page = _get(admin_client, SUBMISSIONS, project=str(partly_done.project_id))
        assert ts.object_errors(page, "Page", generic="SequenceSubmission") == []
        (row,) = page["results"]
        assert "shots" not in row  # TS: only in the detail view
        assert (row["status"], row["shots_total"], row["shots_done"], row["shots_error"]) == ("PARTLY_DONE", 3, 2, 1)
        assert row["video_settings"]["preset"] == VIDEO["preset"]
        assert isinstance(row["routing_note"], (str, type(None)))

    def test_detail_matches_ts_with_its_shots(self, admin_client, ts, partly_done):
        detail = _get(admin_client, f"{SUBMISSIONS}{partly_done.pk}/")
        assert ts.object_errors(detail, "SequenceSubmission") == []
        assert [(s["index"], s["status"]) for s in detail["shots"]] == [(1, "DONE"), (2, "DONE"), (3, "ERROR")]
        assert [s["end_frame"] for s in detail["shots"]] == [4, 10, 24]  # exclusive, as the TS says
        assert detail["shots"][2]["error_message"]

    def test_downloads_flags_follow_the_results(self, admin_client, ts, partly_done):
        detail = _get(admin_client, f"{SUBMISSIONS}{partly_done.pk}/")
        assert set(detail["downloads"]) == set(ts.fields("Downloads"))
        # The whole video waits for every shot; the frames of the DONE shots are there.
        assert detail["downloads"] == dict(NOTHING, frames=True)
        done, _, failed = detail["shots"]
        assert done["downloads"] == dict(NOTHING, video=True, frames=True)
        assert failed["downloads"] == NOTHING

    def test_list_and_detail_agree_on_the_version_downloads(self, admin_client, partly_done):
        row = _get(admin_client, SUBMISSIONS, project=str(partly_done.project_id))["results"][0]
        detail = _get(admin_client, f"{SUBMISSIONS}{partly_done.pk}/")
        assert row["downloads"] == detail["downloads"]

    def test_queued_version_without_results(self, admin_client, ts, unreal, worker_with_token):
        """A version just planned: null items, no downloads at all, PENDING shots."""
        worker, client = worker_with_token
        submission = version(admin_client, unreal, worker, client, sound=False)
        detail = _get(admin_client, f"{SUBMISSIONS}{submission.pk}/")
        assert ts.object_errors(detail, "SequenceSubmission") == []
        assert (detail["status"], detail["video_settings"], detail["video_status"]) == ("QUEUED", None, None)
        assert detail["downloads"] == NOTHING
        assert {s["status"] for s in detail["shots"]} == {"PENDING"}
        assert all(s["downloads"] == NOTHING for s in detail["shots"])


class TestDiscoveryShapes:
    """``Page<SequenceDiscovery>``, ``SequenceDiscovery``, ``PresetFacts`` and ``DiscoveredShot``."""

    def test_list_page_matches_ts_without_shots(self, admin_client, ts, partly_done):
        page = _get(admin_client, DISCOVERIES, project=str(partly_done.project_id))
        assert ts.object_errors(page, "Page", generic="SequenceDiscovery") == []
        (row,) = page["results"]
        assert row["status"] == "DONE"
        assert "shots" not in row and "warnings" not in row  # TS: only in the detail view
        assert ts.object_errors(row["preset_facts"], "PresetFacts") == []

    def test_detail_matches_ts_with_shots(self, admin_client, ts, partly_done):
        detail = _get(admin_client, f"{DISCOVERIES}{partly_done.discovery_id}/")
        assert ts.object_errors(detail, "SequenceDiscovery") == []
        assert [(s["index"], s["name"], s["start"], s["end"]) for s in detail["shots"]] == [
            (1, "sh010", 0, 4), (2, "sh020", 4, 10), (3, "sh030", 20, 24)]
        assert isinstance(detail["warnings"], list)
        assert detail["preset_facts"]["resolution"] == [64, 36]
        assert (detail["playback_start"], detail["playback_end"]) == (0, 24)

    def test_queued_discovery_has_nulls_where_ts_admits_them(self, admin_client, ts, unreal):
        resp = admin_client.post(DISCOVERIES, {
            "asset": unreal.pk, "map": "/Game/Maps/Harbor.Harbor", "sequence": "/Game/Cinematics/Ep02.Ep02",
            "preset": "/Game/Presets/Farm.Farm"}, format="json")
        assert resp.status_code == 201, resp.content
        detail = _get(admin_client, f"{DISCOVERIES}{resp.json()['id']}/")
        assert (detail["status"], detail["playback_start"], detail["playback_end"]) == ("QUEUED", None, None)
        # An unread discovery answers ``{}`` (the model default); the TS admits it as ``Record<string, never>``.
        assert detail["preset_facts"] == {}
        assert set(ts.fields("SequenceDiscovery")) >= {"preset_facts", "shots", "warnings", "error_message"}
        assert "Record<string, never>" in ts.fields("SequenceDiscovery")["preset_facts"][0]
        assert ts.object_errors(detail, "SequenceDiscovery") == []
        assert ts.errors({"can_png": True}, ts.fields("SequenceDiscovery")["preset_facts"][0]) != []
