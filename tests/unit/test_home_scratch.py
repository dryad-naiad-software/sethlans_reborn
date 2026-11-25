# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``tests._home_scratch``: scratch folders, their cleanup, and the sweep of stale ones.

Every test points the helpers at a root under ``tmp_path``; the real
``~/.cache/sethlans-tests`` is never touched here.
"""
import os
import uuid
from pathlib import Path

import pytest

from tests import _home_scratch as scratch

DAY = 24 * 60 * 60


def _entry(root, age, name=None):
    folder = root / (name or uuid.uuid4().hex)
    folder.mkdir(parents=True)
    (folder / "data").mkdir()
    (folder / "data" / "f").write_text("x")
    stamp = 1_000_000_000 - age
    os.utime(folder, (stamp, stamp))
    return folder


class TestRoot:

    def test_root_is_under_the_home_cache(self):
        assert scratch.home_scratch_root() == Path.home() / ".cache" / "sethlans-tests"


class TestScratchFolder:

    def test_unique_uuid_folder_removed_with_its_empty_root(self, tmp_path):
        root = tmp_path / "sethlans-tests"
        gen = scratch.scratch_folder(root)
        folder = next(gen)
        assert folder.parent == root and len(folder.name) == 32 and folder.is_dir()
        (folder / "data").mkdir()
        (folder / "data" / "f").write_text("x")
        with pytest.raises(StopIteration):
            next(gen)
        assert not folder.exists()
        assert not root.exists()

    def test_root_shared_with_another_run_is_kept(self, tmp_path):
        root = tmp_path / "sethlans-tests"
        other = _entry(root, age=0)
        for _folder in scratch.scratch_folder(root):
            pass
        assert root.is_dir() and other.is_dir()

    def test_failed_removal_fails_the_teardown(self, tmp_path, mocker):
        root = tmp_path / "sethlans-tests"
        mocker.patch.object(scratch.shutil, "rmtree")  # removes nothing
        gen = scratch.scratch_folder(root)
        folder = next(gen)
        with pytest.raises(scratch.ScratchCleanupError, match="Could not remove the test scratch folder"):
            next(gen)
        assert folder.exists()

    def test_rmtree_errors_are_reported(self, tmp_path, mocker):
        folder = tmp_path / "x"
        folder.mkdir()

        def failing(path, onexc):
            onexc(os.rmdir, str(path), PermissionError(13, "denied"))

        mocker.patch.object(scratch.shutil, "rmtree", side_effect=failing)
        with pytest.raises(scratch.ScratchCleanupError) as info:
            scratch.remove_tree(folder)
        assert f"{folder}: [Errno 13] denied" in str(info.value)

    def test_unexpected_root_error_is_raised(self, tmp_path, mocker):
        root = tmp_path / "root"
        root.mkdir()
        mocker.patch.object(Path, "rmdir", side_effect=PermissionError(13, "denied"))
        with pytest.raises(scratch.ScratchCleanupError):
            scratch.remove_root_if_empty(root)

    def test_missing_root_is_fine(self, tmp_path):
        scratch.remove_root_if_empty(tmp_path / "missing")


class TestSweepStale:

    NOW = 1_000_000_000

    def test_old_uuid_folders_are_removed_new_ones_kept(self, tmp_path):
        root = tmp_path / "sethlans-tests"
        old = _entry(root, age=DAY + 5)
        new = _entry(root, age=DAY - 5)
        assert scratch.sweep_stale(root, now=self.NOW) == [old]
        assert not old.exists() and new.is_dir()

    def test_exactly_a_day_old_is_stale(self, tmp_path):
        root = tmp_path / "sethlans-tests"
        old = _entry(root, age=DAY)
        assert scratch.sweep_stale(root, now=self.NOW) == [old]

    def test_other_names_files_and_links_are_left(self, tmp_path):
        root = tmp_path / "sethlans-tests"
        named = _entry(root, age=10 * DAY, name="keep-me")
        upper = _entry(root, age=10 * DAY, name=uuid.uuid4().hex.upper())
        loose = root / uuid.uuid4().hex
        loose.write_text("a file with a uuid name")
        os.utime(loose, (0, 0))
        assert scratch.sweep_stale(root, now=self.NOW) == []
        assert named.is_dir() and upper.is_dir() and loose.is_file()

    def test_link_with_a_uuid_name_is_not_followed(self, tmp_path):
        root = tmp_path / "sethlans-tests"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "precious").write_text("x")
        link = root / uuid.uuid4().hex
        try:
            os.symlink(outside, link, target_is_directory=True)
        except OSError as exc:  # Windows without the symlink privilege
            pytest.skip(f"cannot create a symlink here: {exc}")
        assert scratch.sweep_stale(root, now=self.NOW + 100 * DAY) == []
        assert (outside / "precious").is_file()

    def test_empty_root_is_removed_after_the_sweep(self, tmp_path):
        root = tmp_path / "sethlans-tests"
        _entry(root, age=2 * DAY)
        scratch.sweep_stale(root, now=self.NOW)
        assert not root.exists()

    def test_missing_root_sweeps_nothing(self, tmp_path):
        assert scratch.sweep_stale(tmp_path / "missing") == []

    def test_failed_removal_raises(self, tmp_path, mocker):
        root = tmp_path / "sethlans-tests"
        _entry(root, age=2 * DAY)
        mocker.patch.object(scratch.shutil, "rmtree")
        with pytest.raises(scratch.ScratchCleanupError):
            scratch.sweep_stale(root, now=self.NOW)

    def test_default_root_is_the_home_cache(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scratch, "home_scratch_root", lambda: tmp_path / "fake-home-cache")
        old = _entry(tmp_path / "fake-home-cache", age=2 * DAY)
        assert scratch.sweep_stale(now=self.NOW) == [old]
