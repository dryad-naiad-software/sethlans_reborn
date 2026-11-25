# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The data folder's parent folders: the owner/link walk and their owner-only creation."""
import os
import uuid
from pathlib import Path

import pytest

from shared import data_folder, data_folder_rules, owner_only_acl
from tests.unit._off_system_tmp import off_system_tmp_path_fixture
from tests.unit.shared._data_folder_helpers import make_link, override_owner, own_dir

# On macOS pytest's tmp_path is under /private/var, a refused system folder.
tmp_path = off_system_tmp_path_fixture()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("SETHLANS_SETTINGS_DIR", str(tmp_path / "settings"))


def link_message(path):
    return f"{path} is a link or junction above the data folder; give the folder's real path."


def owner_message(path):
    return f"{path} belongs to another account; choose a data folder whose parent folders you or the system own."


class TestParentOwners:

    def test_parent_of_another_account_is_refused(self, tmp_path, mocker):
        parent = own_dir(tmp_path / "shared")
        override_owner(mocker, "is_owned_by_user_or_system", {parent: False})
        assert data_folder.check_data_root(parent / "a" / "data") == [owner_message(parent)]

    def test_the_highest_bad_parent_is_reported(self, tmp_path, mocker):
        top = own_dir(tmp_path / "top")
        lower = own_dir(top / "lower")
        override_owner(mocker, "is_owned_by_user_or_system", {top: False, lower: False})
        assert data_folder.check_data_root(lower / "data") == [owner_message(top)]

    def test_resolved_parents_are_checked_too(self, tmp_path, mocker):
        given = own_dir(tmp_path / "given")
        elsewhere = own_dir(tmp_path / "elsewhere")
        target = given / "data"
        mocker.patch.object(data_folder_rules, "forms", return_value=[target, elsewhere / "data"])
        override_owner(mocker, "is_owned_by_user_or_system", {elsewhere: False})
        assert data_folder_rules.parent_problems(target) == [owner_message(elsewhere)]

    def test_parents_owned_by_the_system_are_fine(self, tmp_path, mocker):
        parent = own_dir(tmp_path / "parent")
        override_owner(mocker, "is_owned_by_current_user", {parent: False})
        override_owner(mocker, "is_owned_by_system", {parent: True})
        assert data_folder.check_data_root(parent / "data") == []

    def test_the_root_of_the_drive_is_never_checked(self, tmp_path, mocker):
        anchor = Path(tmp_path.anchor)
        owners = mocker.patch.object(owner_only_acl, "is_owned_by_user_or_system", return_value=False)
        missing = anchor / f"sethlans-missing-{uuid.uuid4().hex}" / "data"
        assert data_folder.check_data_root(missing) == []
        assert owners.call_count == 0

    def test_missing_parents_are_not_checked(self, tmp_path, mocker):
        owners = mocker.spy(owner_only_acl, "is_owned_by_user_or_system")
        data_folder_rules.parent_problems(tmp_path / "x" / "y" / "data")
        asked = {Path(c.args[0]) for c in owners.call_args_list}
        assert tmp_path / "x" not in asked and tmp_path / "x" / "y" not in asked
        assert tmp_path in asked


class TestParentLinks:
    """A link the test makes is owned by whoever runs it; as root that is the
    system, so the user-owned cases pin the link to a non-system owner."""

    def _user_link(self, mocker, link):
        override_owner(mocker, "is_owned_by_system", {link: False})
        return link

    def test_own_link_parent_is_refused(self, tmp_path, mocker):
        real = own_dir(tmp_path / "real")
        link = self._user_link(mocker, make_link(tmp_path / "link", real))
        assert data_folder.check_data_root(link / "data") == [link_message(link)]

    def test_system_owned_link_parent_is_accepted(self, tmp_path, mocker):
        real = own_dir(tmp_path / "real")
        link = make_link(tmp_path / "link", real)
        override_owner(mocker, "is_owned_by_system", {link: True})
        assert data_folder.check_data_root(link / "data") == []

    @pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() != 0, reason="runs as root only")
    def test_link_made_by_root_is_system_owned_and_accepted(self, tmp_path):
        real = own_dir(tmp_path / "real")
        link = make_link(tmp_path / "link", real)
        assert owner_only_acl.is_owned_by_system(link) is True
        assert data_folder.check_data_root(link / "data") == []

    def test_dangling_link_parent_is_refused(self, tmp_path, mocker):
        gone = own_dir(tmp_path / "gone")
        link = self._user_link(mocker, make_link(tmp_path / "link", gone))
        gone.rmdir()
        assert data_folder.check_data_root(link / "data") == [link_message(link)]

    def test_prepare_refuses_and_creates_nothing_through_the_link(self, tmp_path, mocker):
        real = own_dir(tmp_path / "real")
        link = self._user_link(mocker, make_link(tmp_path / "link", real))
        with pytest.raises(data_folder.DataFolderError) as info:
            data_folder.prepare_data_root(link / "data")
        assert str(info.value) == link_message(link)
        assert list(real.iterdir()) == []


class TestMakeParents:

    def test_missing_levels_are_created_owner_only(self, tmp_path):
        root = tmp_path / "a" / "b" / "c" / "data"
        data_folder.prepare_data_root(root)
        for level in (tmp_path / "a", tmp_path / "a" / "b", tmp_path / "a" / "b" / "c"):
            assert owner_only_acl.verify_owner_only(level), level

    def test_levels_are_made_top_down_one_at_a_time(self, tmp_path, mocker):
        create = mocker.spy(owner_only_acl, "create_owner_only_dir")
        data_folder.prepare_data_root(tmp_path / "a" / "b" / "data")
        made = [Path(c.args[0]) for c in create.call_args_list]
        assert made[:3] == [tmp_path / "a", tmp_path / "a" / "b", tmp_path / "a" / "b" / "data"]

    def test_no_recursive_mkdir_is_used(self, tmp_path, mocker):
        mocker.patch.object(Path, "mkdir", side_effect=AssertionError("Path.mkdir must not be used"))
        mocker.patch("os.makedirs", side_effect=AssertionError("os.makedirs must not be used"))
        assert data_folder.prepare_data_root(tmp_path / "a" / "b" / "data") == tmp_path / "a" / "b" / "data"

    def test_level_turned_into_a_link_is_refused(self, tmp_path, mocker):
        decoy = own_dir(tmp_path / "decoy")
        level = tmp_path / "a"
        real = owner_only_acl.create_owner_only_dir

        def create(path):
            if Path(path) == level:
                make_link(level, decoy)
                return True
            return real(path)

        mocker.patch.object(owner_only_acl, "create_owner_only_dir", side_effect=create)
        with pytest.raises(data_folder.DataFolderError) as info:
            data_folder.prepare_data_root(level / "b" / "data")
        assert str(info.value) == link_message(level)
        assert list(decoy.iterdir()) == []

    def test_level_made_first_by_another_account_is_refused(self, tmp_path, mocker):
        level = tmp_path / "a"
        real = owner_only_acl.create_owner_only_dir

        def create(path):
            if Path(path) == level:
                real(path)
                return False  # someone else won the race
            return real(path)

        mocker.patch.object(owner_only_acl, "create_owner_only_dir", side_effect=create)
        override_owner(mocker, "is_owned_by_user_or_system", {level: False})
        with pytest.raises(data_folder.DataFolderError) as info:
            data_folder.prepare_data_root(level / "data")
        assert str(info.value) == owner_message(level)
        assert not (level / "data").exists()

    def test_level_made_first_by_this_user_is_fine(self, tmp_path, mocker):
        level = tmp_path / "a"
        real = owner_only_acl.create_owner_only_dir

        def create(path):
            real(path)
            return Path(path) != level

        mocker.patch.object(owner_only_acl, "create_owner_only_dir", side_effect=create)
        assert data_folder.prepare_data_root(level / "data") == level / "data"
