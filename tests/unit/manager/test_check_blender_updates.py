# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``check_blender_updates``: ``resolved_version`` only moves forward (BS-REL-9, BS-REL-10).

The supported-version rows, the clock and ``latest_patch`` are mocked; no
database and no network. Two series are checked per run: 4.5 (the case under
test) and 4.6 (always up to date), so every case also shows the run goes on
to the next series.
"""
import io
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from shared.blender_sources import AllSourcesFailedError
from workers.management.commands import check_blender_updates as command_module

NOW = datetime(2026, 11, 26, tzinfo=timezone.utc)
NEXT_SERIES_LINE = "  4.6: up to date (4.6.0)"


@pytest.mark.parametrize("answer, moved, stdout_line", [
    ("4.5.10", 1, "  Updated 4.5: 4.5.9 -> 4.5.10"),
    ("4.5.10", 0, "  4.5: changed meanwhile by another writer; 4.5.10 not written"),
    ("4.5.8", None, "  4.5: up to date (4.5.9)"),
    (AllSourcesFailedError("Blender release list (series page) 4.5", [("https://a.test/", "HTTP 403")]),
     None, None),
], ids=["newer-patch", "changed-meanwhile", "lagging-mirror", "lookup-failed"])
def test_resolved_version_moves_only_to_a_newer_patch(mocker, answer, moved, stdout_line):
    """BS-REL-10: 4.5.10 replaces 4.5.9 (integer tuple, conditional on the value read); when another
    writer changed the row meanwhile nothing is moved and the stale value is not reported as up to date;
    4.5.8 from a mirror behind the official site is ignored. BS-REL-9: a failed lookup is a warning. In
    every case the check time is written and the next series is checked."""
    rows = {7: mocker.MagicMock(), 8: mocker.MagicMock()}
    rows[7].filter.return_value.update.return_value = moved
    objects = mocker.patch.object(command_module.SupportedBlenderVersion, "objects")
    objects.filter.side_effect = lambda pk: rows[pk]
    objects.all.return_value.__iter__.return_value = iter([
        SimpleNamespace(pk=7, series="4.5", resolved_version="4.5.9"),
        SimpleNamespace(pk=8, series="4.6", resolved_version="4.6.0"),
    ])
    mocker.patch.object(command_module.timezone, "now", return_value=NOW)
    answers = {"4.5": answer, "4.6": "4.6.0"}

    def lookup(series, timeout):
        assert timeout == command_module.LOOKUP_TIMEOUT
        if isinstance(answers[series], Exception):
            raise answers[series]
        return answers[series]
    mocker.patch.object(command_module, "latest_patch", side_effect=lookup)
    stdout, stderr = io.StringIO(), io.StringIO()

    command_module.Command(stdout=stdout, stderr=stderr).handle()

    assert stdout.getvalue().splitlines() == [line for line in (stdout_line, NEXT_SERIES_LINE) if line]
    assert ("WARNING: Could not check 4.5" in stderr.getvalue()) is (stdout_line is None)
    if moved is None:
        rows[7].filter.assert_not_called()
    else:
        rows[7].filter.assert_called_once_with(resolved_version="4.5.9")
        rows[7].filter.return_value.update.assert_called_once_with(resolved_version="4.5.10", last_patch_check=NOW)
    if moved == 1:
        rows[7].update.assert_not_called()  # the conditional update wrote the check time
    else:
        rows[7].update.assert_called_once_with(last_patch_check=NOW)
    rows[8].update.assert_called_once_with(last_patch_check=NOW)
