# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``CACHE_DIR`` rules, adoption and trust (Unreal foundation FR-WC-3, FR-WC-4,
Sec S1/S2; FR-T-5 "config_unreal" cache-dir rejection and "Owner-only ACL").

The ``TestPrepare`` cases use the real owner-only ACL on Windows.
"""
import os
import sys

import pytest

from sethlans_worker_agent.unreal_executor import cache_dir
from shared import owner_only_acl

windows_only = pytest.mark.skipif(sys.platform != 'win32', reason='CACHE_DIR rules are Windows paths (phase 1)')


class TestLexicalRules:

    @pytest.mark.parametrize('path', [
        r'D:\uecache', r'C:\Users\mestrella\AppData\Local\Sethlans\worker\unreal', r'E:\a_b.c-d',
    ])
    def test_accepts(self, path):
        cache_dir.check_cache_dir_text(path, env={})

    @pytest.mark.parametrize('path', [
        r'C:\Users\Mario Estrella\AppData\Local\Sethlans\worker\unreal',  # whitespace
        r'D:\ue,cache', r'D:\ue;cache', r'D:\ue"cache', "D:\\ue'cache",
        r'\\nas\sethlans\cache', r'\\?\D:\uecache', r'\\.\D:\uecache',
        'uecache', '/srv/uecache', r'D:\ü', 'D:\\uecache\n',
        'D:\\' + 'a' * 120,
    ])
    def test_rejects(self, path):
        with pytest.raises(cache_dir.CacheDirError):
            cache_dir.check_cache_dir_text(path, env={})

    @pytest.mark.parametrize('path', ['D:\\', 'd:\\\\'])
    def test_rejects_drive_root(self, path):
        with pytest.raises(cache_dir.CacheDirError, match='drive root'):
            cache_dir.check_cache_dir_text(path, env={})

    @pytest.mark.parametrize('var', ['OneDrive', 'OneDriveCommercial', 'OneDriveConsumer'])
    def test_rejects_onedrive(self, var):
        env = {var: r'C:\Users\me\OneDrive'}
        with pytest.raises(cache_dir.CacheDirError, match='OneDrive'):
            cache_dir.check_cache_dir_text(r'c:\users\ME\onedrive\cache', env=env)
        with pytest.raises(cache_dir.CacheDirError, match='OneDrive'):
            cache_dir.check_cache_dir_text(r'C:\Users\me\OneDrive', env=env)
        cache_dir.check_cache_dir_text(r'C:\Users\me\OneDriveX\cache', env=env)

    def test_unc_rejected_before_any_filesystem_call(self, monkeypatch):
        def boom(*args, **kwargs):
            raise AssertionError('filesystem touched')

        monkeypatch.setattr(os.path, 'realpath', boom)
        monkeypatch.setattr(os.path, 'islink', boom)
        with pytest.raises(cache_dir.CacheDirError):
            cache_dir.resolve_cache_dir(r'\\attacker\share\cache', env={})


class FakeAcl:
    def __init__(self, verify_results, apply_error=None):
        self.verify_results = list(verify_results)
        self.apply_error = apply_error
        self.applied = 0

    def verify_owner_only(self, path):
        return self.verify_results.pop(0)

    def apply_owner_only(self, path):
        self.applied += 1
        if self.apply_error:
            raise self.apply_error


@pytest.fixture()
def fake_acl(monkeypatch):
    def install(*verify_results, apply_error=None):
        fake = FakeAcl(verify_results, apply_error)
        monkeypatch.setattr(owner_only_acl, 'verify_owner_only', fake.verify_owner_only)
        monkeypatch.setattr(owner_only_acl, 'apply_owner_only', fake.apply_owner_only)
        return fake
    return install


class TestEnsureOwnerOnly:

    def test_verified_needs_no_apply(self, fake_acl, tmp_path):
        fake = fake_acl(True)
        assert cache_dir.ensure_owner_only(tmp_path) == (True, False)
        assert fake.applied == 0

    def test_reapplies_once(self, fake_acl, tmp_path):
        fake = fake_acl(False, True)
        assert cache_dir.ensure_owner_only(tmp_path) == (True, True)
        assert fake.applied == 1

    def test_fails_closed(self, fake_acl, tmp_path):
        fake_acl(False, False)
        assert cache_dir.ensure_owner_only(tmp_path) == (False, True)
        fake_acl(False, apply_error=OSError(5, 'denied'))
        assert cache_dir.ensure_owner_only(tmp_path) == (False, True)


@pytest.fixture()
def base(tmp_path):
    if not cache_dir.ALLOWLIST.match(str(tmp_path)) or len(str(tmp_path)) > cache_dir.MAX_LENGTH - 20:
        pytest.skip('tmp_path is not a valid CACHE_DIR on this machine (spaces or too long)')
    return tmp_path


def _prepare_and_complete(path):
    prepared = cache_dir.prepare_cache_dir(str(path), env={})
    cache_dir.complete_cache_dir(prepared)
    return prepared


@windows_only
class TestPrepare:

    def test_new_dir_is_owner_only_from_birth_and_trusted(self, base):
        target = base / 'uecache'
        prepared = cache_dir.prepare_cache_dir(str(target), env={})
        assert prepared.path == target and prepared.created and prepared.content_trusted
        assert not (target / cache_dir.ACL_MARKER).exists()  # no write before the owner lock
        assert owner_only_acl.verify_owner_only(target)
        cache_dir.complete_cache_dir(prepared)
        assert (target / cache_dir.ACL_MARKER).is_file()
        again = _prepare_and_complete(target)
        assert again.created is False and again.content_trusted is True

    def test_parents_are_created(self, base):
        assert _prepare_and_complete(base / 'a' / 'uecache').created

    def test_existing_empty_dir_without_marker_stays_untrusted(self, base):
        target = base / 'uecache'
        target.mkdir()  # inherited, looser ACL: adopted only as untrusted
        first = _prepare_and_complete(target)
        assert first.content_trusted is False and not first.created
        assert not (target / cache_dir.ACL_MARKER).exists()
        second = _prepare_and_complete(target)  # S2: a restart before recovery stays untrusted
        assert second.content_trusted is False
        assert owner_only_acl.verify_owner_only(target)

    def test_mark_content_trusted_after_recovery(self, base):
        target = base / 'uecache'
        target.mkdir()
        prepared = _prepare_and_complete(target)
        trusted = cache_dir.mark_content_trusted(prepared)
        assert trusted.content_trusted is True
        assert _prepare_and_complete(target).content_trusted is True

    def test_non_empty_dir_without_marker_is_refused(self, base):
        target = base / 'uecache'
        (target / 'p').mkdir(parents=True)
        with pytest.raises(cache_dir.CacheDirError, match='not created by Sethlans'):
            cache_dir.prepare_cache_dir(str(target), env={})

    def test_our_lock_file_alone_does_not_count_as_content(self, base):
        target = base / 'uecache'
        target.mkdir()
        (target / cache_dir.OWNER_LOCK_NAME).write_bytes(b'')
        assert _prepare_and_complete(target).content_trusted is False

    def test_dir_owned_by_another_account_is_refused_and_untouched(self, base, monkeypatch):
        target = base / 'uecache'
        target.mkdir()
        applied = []
        monkeypatch.setattr(owner_only_acl, 'is_owned_by_current_user', lambda path: False)
        monkeypatch.setattr(owner_only_acl, 'apply_owner_only', lambda path: applied.append(path))
        with pytest.raises(cache_dir.CacheDirError, match='owned by another account'):
            cache_dir.prepare_cache_dir(str(target), env={})
        assert applied == []  # never taken over

    def test_reapplied_acl_drops_the_marker(self, base, monkeypatch):
        target = base / 'uecache'
        _prepare_and_complete(target)
        real_verify = owner_only_acl.verify_owner_only
        calls = []

        def first_check_fails(path):
            calls.append(path)
            return False if len(calls) == 1 else real_verify(path)

        monkeypatch.setattr(owner_only_acl, 'verify_owner_only', first_check_fails)
        prepared = _prepare_and_complete(target)
        assert prepared.content_trusted is False
        assert not (target / cache_dir.ACL_MARKER).exists()

    def test_acl_failure_disables(self, base, monkeypatch):
        monkeypatch.setattr(owner_only_acl, 'verify_owner_only', lambda path: False)
        with pytest.raises(cache_dir.CacheDirError, match=cache_dir.ACL_REASON):
            cache_dir.prepare_cache_dir(str(base / 'c'), env={})

    def test_a_file_is_not_a_cache_dir(self, base):
        target = base / 'file'
        target.write_text('x')
        with pytest.raises(cache_dir.CacheDirError, match='not a directory'):
            cache_dir.prepare_cache_dir(str(target), env={})

    def test_junction_is_refused(self, base):
        import _winapi
        real = base / 'real'
        real.mkdir()
        link = base / 'link'
        _winapi.CreateJunction(str(real), str(link))
        with pytest.raises(cache_dir.CacheDirError, match='junction'):
            cache_dir.prepare_cache_dir(str(link), env={})

    def test_junction_ancestor_is_refused(self, base):
        import _winapi
        real = base / 'real'
        real.mkdir()
        _winapi.CreateJunction(str(real), str(base / 'link'))
        with pytest.raises(cache_dir.CacheDirError, match='junction'):
            cache_dir.prepare_cache_dir(str(base / 'link' / 'uecache'), env={})


def test_not_a_fixed_disk_is_refused_before_realpath(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError('filesystem touched')

    monkeypatch.setattr(cache_dir.detection, 'is_fixed_drive', lambda path: False)
    monkeypatch.setattr(os.path, 'realpath', boom)
    monkeypatch.setattr(os.path, 'islink', boom)
    with pytest.raises(cache_dir.CacheDirError, match='fixed local disk'):
        cache_dir._check_no_links(r'V:\uecache', {})


@windows_only
class TestCompleteRechecks:
    """Leaf junction swap between the preparation checks (security re-review)."""

    def test_owner_changed_after_prepare_is_refused(self, base, monkeypatch):
        target = base / 'uecache'
        target.mkdir()
        answers = iter([True, False])
        monkeypatch.setattr(owner_only_acl, 'is_owned_by_current_user', lambda path: next(answers))
        prepared = cache_dir.prepare_cache_dir(str(target), env={})
        with pytest.raises(cache_dir.CacheDirError, match='owned by another account'):
            cache_dir.complete_cache_dir(prepared)
        assert sorted(os.listdir(target)) == []  # no marker, no write test

    def test_acl_changed_after_prepare_is_refused(self, base, monkeypatch):
        prepared = cache_dir.prepare_cache_dir(str(base / 'uecache'), env={})
        monkeypatch.setattr(owner_only_acl, 'verify_owner_only', lambda path: False)
        with pytest.raises(cache_dir.CacheDirError, match=cache_dir.ACL_REASON):
            cache_dir.complete_cache_dir(prepared)
        assert not (prepared.path / cache_dir.ACL_MARKER).exists()

    def test_junction_swapped_in_after_prepare_is_refused(self, base):
        import _winapi
        target = base / 'uecache'
        prepared = cache_dir.prepare_cache_dir(str(target), env={})
        target.rmdir()
        other = base / 'other'
        other.mkdir()
        _winapi.CreateJunction(str(other), str(target))
        with pytest.raises(cache_dir.CacheDirError, match='junction'):
            cache_dir.complete_cache_dir(prepared)
        assert os.listdir(other) == []

    def test_marker_owned_by_another_account_does_not_count(self, base, monkeypatch):
        target = base / 'uecache'
        _prepare_and_complete(target)
        (target / 'p').mkdir()
        real = owner_only_acl.is_owned_by_current_user
        monkeypatch.setattr(owner_only_acl, 'is_owned_by_current_user',
                            lambda path: False if str(path).endswith(cache_dir.ACL_MARKER) else real(path))
        with pytest.raises(cache_dir.CacheDirError, match='not created by Sethlans'):
            cache_dir.prepare_cache_dir(str(target), env={})
