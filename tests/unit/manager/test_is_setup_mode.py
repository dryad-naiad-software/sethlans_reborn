# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``workers.services.sentinel.is_setup_mode`` (FR-22c)."""

from __future__ import annotations

import json

from workers.services.sentinel import (
    SENTINEL_FILENAME,
    SENTINEL_VERSION,
    create_sentinel,
    is_setup_complete,
    is_setup_mode,
)


class TestIsSetupMode:

    def test_true_when_sentinel_missing(self, tmp_path):
        assert is_setup_mode(tmp_path) is True

    def test_false_when_sentinel_complete(self, tmp_path):
        create_sentinel(tmp_path, "manager", ["verified"])
        assert is_setup_mode(tmp_path) is False

    def test_true_when_sentinel_exists_but_invalid_version(self, tmp_path):
        # Current implementation reads via read_sentinel which rejects
        # unknown versions (returns None -> is_setup_complete is False
        # -> is_setup_mode is True).  Intentional: the view treats a
        # broken sentinel as "still in setup".
        (tmp_path / SENTINEL_FILENAME).write_text(
            json.dumps({"version": 999}), encoding="utf-8",
        )
        assert is_setup_mode(tmp_path) is True

    def test_malformed_sentinel_treated_as_incomplete(self, tmp_path):
        (tmp_path / SENTINEL_FILENAME).write_text(
            "{not-json!", encoding="utf-8",
        )
        assert is_setup_mode(tmp_path) is True

    def test_true_when_sentinel_has_null_completed_at(self, tmp_path):
        # A mid-wizard sentinel written by ``append_checkpoint`` has
        # ``completed_at=None``.  ``is_setup_complete`` must NOT claim
        # completion until the final ``create_sentinel`` writes a
        # truthy timestamp; otherwise this helper would disagree with
        # ``SetupGateMiddleware._check_sentinel`` during the wizard.
        (tmp_path / SENTINEL_FILENAME).write_text(
            json.dumps({
                "version": SENTINEL_VERSION,
                "completed_at": None,
                "topology": None,
                "checkpoints": ["topology_chosen"],
            }),
            encoding="utf-8",
        )
        assert is_setup_mode(tmp_path) is True


class TestIsSetupComplete:
    """Tighter semantics: ``completed_at`` must be a truthy string."""

    def test_false_when_sentinel_missing(self, tmp_path):
        assert is_setup_complete(tmp_path) is False

    def test_false_when_completed_at_none(self, tmp_path):
        # Checkpoint-only sentinel — not yet complete.
        (tmp_path / SENTINEL_FILENAME).write_text(
            json.dumps({
                "version": SENTINEL_VERSION,
                "completed_at": None,
                "topology": "manager",
                "checkpoints": ["topology_chosen"],
            }),
            encoding="utf-8",
        )
        assert is_setup_complete(tmp_path) is False

    def test_true_when_completed_at_truthy(self, tmp_path):
        (tmp_path / SENTINEL_FILENAME).write_text(
            json.dumps({
                "version": SENTINEL_VERSION,
                "completed_at": "2025-01-15T12:00:00Z",
                "topology": "manager",
                "checkpoints": ["verified"],
            }),
            encoding="utf-8",
        )
        assert is_setup_complete(tmp_path) is True

    def test_false_when_completed_at_missing_key(self, tmp_path):
        # Defensive: even if the key itself is absent the helper must
        # treat this as incomplete.
        (tmp_path / SENTINEL_FILENAME).write_text(
            json.dumps({
                "version": SENTINEL_VERSION,
                "topology": "manager",
                "checkpoints": [],
            }),
            encoding="utf-8",
        )
        assert is_setup_complete(tmp_path) is False


class TestIsSetupModeInversion:
    """``is_setup_mode`` must be the strict logical inverse of
    ``is_setup_complete`` in every case."""

    def test_inversion_when_missing(self, tmp_path):
        assert is_setup_mode(tmp_path) is (not is_setup_complete(tmp_path))

    def test_inversion_when_checkpoint_only(self, tmp_path):
        (tmp_path / SENTINEL_FILENAME).write_text(
            json.dumps({
                "version": SENTINEL_VERSION,
                "completed_at": None,
                "topology": "manager",
                "checkpoints": ["topology_chosen"],
            }),
            encoding="utf-8",
        )
        assert is_setup_mode(tmp_path) is (not is_setup_complete(tmp_path))
        assert is_setup_mode(tmp_path) is True

    def test_inversion_when_complete(self, tmp_path):
        create_sentinel(tmp_path, "manager", ["verified"])
        assert is_setup_mode(tmp_path) is (not is_setup_complete(tmp_path))
        assert is_setup_mode(tmp_path) is False
