# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``tools/dev_worker.py`` seeds a usable worker page password (setup-rebuild FR-WK-3, FR-WK-4).

The dev worker has no default password any more than a real one, so its
default must pass the page password rules, and the seeded value must be the
hash format the worker checks, in the dev ``config.json``.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_seeded_default_passes_the_rules_and_verifies(tmp_path):
    """FR-WK-3, FR-WK-4: the default passes the rules; the seed keeps other keys and stores a checkable hash."""
    dev_worker = importlib.import_module("tools.dev_worker")
    from sethlans_worker_agent.web_ui import password_hash, password_rules

    (tmp_path / "config.json").write_text(json.dumps({"manager": {"host": "m"}, "worker": {"x": 1}}),
                                          encoding="utf-8")
    dev_worker._seed_ui_password(dev_worker.DEFAULT_UI_PASSWORD, tmp_path)

    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert password_rules.problems(dev_worker.DEFAULT_UI_PASSWORD, "render-node") == []
    assert saved["manager"] == {"host": "m"} and saved["worker"]["x"] == 1
    assert password_hash.verify(dev_worker.DEFAULT_UI_PASSWORD, saved["worker"]["ui_password_hash"]) is True


def test_bad_ui_password_is_refused_without_echo(tmp_path, monkeypatch, capsys):
    """FR-WK-4: ``--ui-password`` goes through the worker's rules; a bad one exits non-zero, seeds nothing."""
    dev_worker = importlib.import_module("tools.dev_worker")

    def _no_launch(*_args, **_kwargs):
        raise AssertionError("worker launched with a refused password")

    monkeypatch.setenv("SETHLANS_DEV_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(dev_worker.common, "stream_subprocess", _no_launch)

    assert dev_worker.main(["--ui-password", "12345678"]) == 2

    err = capsys.readouterr().err
    assert "too common" in err and "entirely numeric" in err
    assert "12345678" not in err
    assert not (tmp_path / "worker" / "config.json").exists()
