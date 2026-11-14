# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The executor-facing entry points (Unreal foundation FR-UE-2 phases 2-4
and 10, FR-PA-2 read-only share, FR-PA-10 release order, Sec Low-12).
"""
import builtins
import os

import pytest

from sethlans_worker_agent.project_archive import integrity, overlay, share_copy, workspace
from sethlans_worker_agent.project_archive import working_copy as wc
from sethlans_worker_agent.project_archive.errors import OverlayPinMismatchError, ShareUnavailableError

from . import _helpers as h


@pytest.fixture()
def share(tmp_path):
    root = tmp_path / 'share'
    (root / 'incoming').mkdir(parents=True)
    pin = h.make_zip(root / 'incoming' / 'Demo.zip')
    fixes = root / 'project-fixes'
    (fixes / 'Config').mkdir(parents=True)
    (fixes / 'Config' / 'DefaultEngine.ini').write_bytes(b'[Fixed]\n')
    return root, pin, h.spec_digest(fixes)


def _request(pin, ov_pin='', overlay_path='project-fixes', **kwargs):
    values = dict(job_id=42, attempt='1a2b3c4d', share_root=h.SHARE_ROOT, archive_relpath='incoming/Demo.zip',
                  archive_sha256=pin, uproject_relpath='Demo/Demo.uproject',
                  overlay_relpath=overlay_path if ov_pin else '', overlay_sha256=ov_pin)
    values.update(kwargs)
    return workspace.ArchiveRequest(**values)


def _prepare(cache, share_root, request, reach=None, **kwargs):
    options = dict(limits=h.limits(), reachability=reach or h.AlwaysReachable(),
                   resolve=h.local_resolver(share_root), share_validator=lambda p: p,
                   overlay_validator=lambda p: p, digest_fn=h.spec_digest)
    options.update(kwargs)
    return workspace.prepare_workspace(cache, request, **options)


def test_miss_then_hit_with_overlay(tmp_path, share):
    share_root, pin, ov_pin = share
    cache = h.make_cache(tmp_path)
    reach = h.AlwaysReachable()
    first = _prepare(cache, share_root, _request(pin, ov_pin), reach)
    assert first.summary['cache'] == 'miss' and first.summary['workcopy'] == 'hardlink'
    assert first.summary['overlay_sha256'] == ov_pin and first.summary['overlay_files'] == '1'
    assert (first.working_copy.project_dir / 'Config' / 'DefaultEngine.ini').read_bytes() == b'[Fixed]\n'
    assert reach.calls == [('incoming/Demo.zip', 'file'), ('project-fixes', 'dir')]
    report = first.finish()
    assert report.summary == 'pristine_integrity=ok' and first.summary['pristine_integrity'] == 'ok'
    assert not first.working_copy.root.exists() and not first.overlay.path.exists()
    assert cache.refcount(pin) == 0
    reach.calls.clear()
    second = _prepare(cache, share_root, _request(pin, ov_pin), reach)
    assert second.summary['cache'] == 'hit'
    assert reach.calls == [('project-fixes', 'dir')]  # a hit needs no share access for the archive
    second.finish()


def test_finish_order_is_integrity_then_cleanup_then_release(tmp_path, share, monkeypatch):
    share_root, pin, _ov = share
    cache = h.make_cache(tmp_path)
    ws = _prepare(cache, share_root, _request(pin))
    calls = []
    real_check, real_remove, real_release = integrity.check_after_job, wc.remove, cache.release
    monkeypatch.setattr(integrity, 'check_after_job', lambda *a, **k: calls.append('integrity') or real_check(*a, **k))
    monkeypatch.setattr(wc, 'remove', lambda *a, **k: calls.append('remove') or real_remove(*a, **k))
    monkeypatch.setattr(cache, 'release', lambda sha: calls.append('release') or real_release(sha))
    ws.finish()
    assert calls == ['integrity', 'remove', 'release']
    assert ws.finish().summary == 'pristine_integrity=skipped(already finished)'


def test_survivors_skip_the_integrity_check(tmp_path, share):
    share_root, pin, _ov = share
    cache = h.make_cache(tmp_path)
    ws = _prepare(cache, share_root, _request(pin))
    assert ws.finish(survivors=True).summary == 'pristine_integrity=skipped(survivors)'
    assert cache.refcount(pin) == 0


def test_overlay_pin_mismatch_fails_before_materialization(tmp_path, share):
    share_root, pin, _ov = share
    cache = h.make_cache(tmp_path)
    with pytest.raises(OverlayPinMismatchError):
        _prepare(cache, share_root, _request(pin, '2' * 64))
    assert list(cache.layout.w.iterdir()) == [] and list(cache.layout.o.iterdir()) == []
    assert cache.refcount(pin) == 0 and cache.state(pin) == 'ready'


class Unreachable:
    def check(self, *args, **kwargs):
        return False


def test_unreachable_archive_is_a_share_failure(tmp_path, share):
    share_root, pin, _ov = share
    cache = h.make_cache(tmp_path)
    with pytest.raises(ShareUnavailableError):
        _prepare(cache, share_root, _request(pin), Unreachable())
    assert list(cache.layout.p.iterdir()) == []


def test_share_paths_are_only_ever_read(tmp_path, share, monkeypatch):
    """FR-PA-2: no write-capable call targets a path under the share."""
    share_root, pin, ov_pin = share
    cache = h.make_cache(tmp_path)
    before = {p: os.stat(p).st_mtime_ns for p in share_root.rglob('*')}
    touched = []
    real_open = builtins.open
    under = os.path.normcase(str(share_root))

    def on_share(path):
        return os.path.normcase(os.fspath(path)).replace('\\\\?\\', '').startswith(under)

    def guarded_open(path, mode='r', *args, **kwargs):
        if isinstance(path, (str, os.PathLike)) and on_share(path) and mode not in ('r', 'rb'):
            touched.append(('open', path, mode))
        return real_open(path, mode, *args, **kwargs)

    for module in (builtins, share_copy, overlay):
        monkeypatch.setattr(module, 'open', guarded_open, raising=False)
    for name in ('replace', 'remove', 'unlink', 'utime', 'rename', 'chmod', 'rmdir'):
        real = getattr(os, name)
        monkeypatch.setattr(os, name, lambda *a, _real=real, _name=name, **k: (
            touched.append((_name, a[0])) if a and on_share(a[0]) else None) or _real(*a, **k))
    ws = _prepare(cache, share_root, _request(pin, ov_pin))
    ws.finish()
    assert touched == []
    assert {p: os.stat(p).st_mtime_ns for p in share_root.rglob('*')} == before


def test_production_call_sites_use_the_default_validators(tmp_path, share, monkeypatch):
    """Sec Low-12: only tests inject a share validator."""
    share_root, pin, ov_pin = share
    cache = h.make_cache(tmp_path)
    seen = {}

    def fake_copy(unc, dest, pin_, abort, **kwargs):
        seen['copy'] = kwargs['share_validator']
        raise ShareUnavailableError('stop here')

    monkeypatch.setattr(share_copy, 'copy_archive', fake_copy)
    with pytest.raises(ShareUnavailableError):
        workspace.prepare_workspace(cache, _request(pin), limits=h.limits(), reachability=h.AlwaysReachable(),
                                    resolve=h.local_resolver(share_root))
    assert seen['copy'] is share_copy.default_share_validator
    defaults = workspace.prepare_workspace.__kwdefaults__
    assert defaults['share_validator'] is share_copy.default_share_validator
    assert defaults['overlay_validator'] is overlay.default_share_validator
    assert defaults['digest_fn'] is overlay.default_digest
    assert defaults['resolve'] is workspace.default_resolve


@pytest.mark.parametrize('where', ['overlay', 'pristine'])
def test_zen_guard_sees_overlay_and_pristine_config(tmp_path, share, where):
    """FR-UE-24 runs on the materialized working copy (FR-UE-2 step 4a)."""
    from sethlans_worker_agent.unreal_executor import zen_guard
    share_root, pin, ov_pin = share
    if where == 'overlay':
        (share_root / 'project-fixes' / 'Config' / 'DefaultGame.ini').write_bytes(b'[Zen.AutoLaunch]\n+DATAPATH=x\n')
        ov_pin = h.spec_digest(share_root / 'project-fixes')
    else:
        files = dict(h.PROJECT_FILES)
        files['Demo/Config/DefaultGame.ini'] = b'[Zen]\nDataPath=D:\\x\n'
        pin = h.make_zip(share_root / 'incoming' / 'Demo.zip', files=files)
    cache = h.make_cache(tmp_path)
    ws = _prepare(cache, share_root, _request(pin, ov_pin))
    result = zen_guard.scan_project_config(ws.working_copy.project_dir)
    assert result.summary == 'zen_ddc_guard=FAILED'
    assert [hit.relpath for hit in result.hits] == ['Config/DefaultGame.ini']
    ws.finish()


def test_bad_attempt_token_rejected(tmp_path, share):
    share_root, pin, _ov = share
    cache = h.make_cache(tmp_path)
    with pytest.raises(ValueError):
        _prepare(cache, share_root, _request(pin, attempt='../../x'))
