# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Overlay staging and pin (Unreal foundation FR-PA-12, FR-T-6 "Overlay").
"""
import io
import os
import sys

import pytest

from sethlans_worker_agent.project_archive import eviction, overlay
from sethlans_worker_agent.project_archive.abort import Abort
from sethlans_worker_agent.project_archive.errors import OverlayPinMismatchError, UnsafeOverlayError

from . import _helpers as h


def _overlay_source(tmp_path):
    src = tmp_path / 'share' / 'project-fixes'
    (src / 'Config').mkdir(parents=True)
    (src / 'Content' / 'Python').mkdir(parents=True)
    (src / 'Config' / 'DefaultEngine.ini').write_bytes(b'[Fixed]\nA=1\n')
    (src / 'Content' / 'Python' / 'auto_open_widget.py').write_bytes(b'print("hi")\n')
    return src


def _stage(cache, src, pin, **kwargs):
    options = dict(reserve=lambda n: eviction.reserve(cache, n), share_validator=lambda p: str(p),
                   digest_fn=h.spec_digest)
    options.update(kwargs)
    return overlay.stage_overlay(str(src), cache.layout.overlay_dir(42, 'abcdef01'), pin, Abort(), **options)


def test_staged_copy_digest_matches_the_pin(tmp_path):
    src = _overlay_source(tmp_path)
    cache = h.make_cache(tmp_path)
    pin = h.spec_digest(src)
    staged = _stage(cache, src, pin)
    assert staged.digest == pin
    assert staged.files == ('Config/DefaultEngine.ini', 'Content/Python/auto_open_widget.py')
    assert (staged.path / 'Config' / 'DefaultEngine.ini').read_bytes() == b'[Fixed]\nA=1\n'
    assert staged.path == cache.layout.o / '42-abcdef01'
    assert cache.ledger.in_flight_reserved == 0


def test_digest_equals_the_shared_function(tmp_path):
    digest_module = pytest.importorskip('shared.unreal.overlay_digest')
    src = _overlay_source(tmp_path)
    cache = h.make_cache(tmp_path)
    pin = digest_module.compute_overlay_digest(src).digest
    staged = _stage(cache, src, pin, digest_fn=overlay.default_digest)
    assert staged.digest == pin == h.spec_digest(src)


def test_overlay_pin_mismatch(tmp_path):
    src = _overlay_source(tmp_path)
    cache = h.make_cache(tmp_path)
    with pytest.raises(OverlayPinMismatchError, match=r'^Overlay SHA-256 mismatch: expected 1111111111111111…, got '):
        _stage(cache, src, '1' * 64)


def test_cap_counts_bytes_while_copying(tmp_path, monkeypatch):
    src = _overlay_source(tmp_path)
    cache = h.make_cache(tmp_path)
    real_open = open

    def growing_open(path, mode='r', *args, **kwargs):
        if str(path).startswith(str(src)) and mode == 'rb':
            return io.BytesIO(b'g' * 5000)  # the file grew after it was stat'ed
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(overlay, 'open', growing_open, raising=False)
    with pytest.raises(UnsafeOverlayError, match='larger than 1000 bytes'):
        _stage(cache, src, h.spec_digest(src), max_bytes=1000)


def test_declared_size_over_cap_rejected_before_copy(tmp_path):
    src = _overlay_source(tmp_path)
    (src / 'Content' / 'big.bin').write_bytes(b'b' * 2000)
    cache = h.make_cache(tmp_path)
    with pytest.raises(UnsafeOverlayError, match='larger than'):
        _stage(cache, src, h.spec_digest(src), max_bytes=1000)
    assert not (cache.layout.o / '42-abcdef01').exists()


def test_too_many_files(tmp_path):
    src = _overlay_source(tmp_path)
    cache = h.make_cache(tmp_path)
    with pytest.raises(UnsafeOverlayError, match='more than 1 files'):
        _stage(cache, src, h.spec_digest(src), max_files=1)


def test_short_name_component_rejected(tmp_path):
    src = _overlay_source(tmp_path)
    (src / 'Content' / 'PROGRA~1.txt').write_bytes(b'x')
    cache = h.make_cache(tmp_path)
    with pytest.raises(UnsafeOverlayError, match='8.3'):
        _stage(cache, src, h.spec_digest(src))


def _plant_dir_link(link, target):
    if sys.platform == 'win32':
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_link_in_overlay_rejected(tmp_path):
    src = _overlay_source(tmp_path)
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    (elsewhere / 'secret.txt').write_bytes(b's')
    _plant_dir_link(src / 'Content' / 'linked', elsewhere)
    cache = h.make_cache(tmp_path)
    with pytest.raises(UnsafeOverlayError, match='symlink or junction'):
        _stage(cache, src, h.spec_digest(src))


def test_overlay_root_link_rejected(tmp_path):
    src = _overlay_source(tmp_path)
    link = tmp_path / 'share' / 'linked-overlay'
    _plant_dir_link(link, src)
    cache = h.make_cache(tmp_path)
    with pytest.raises(UnsafeOverlayError, match='not a plain directory'):
        _stage(cache, link, h.spec_digest(src))


class _Blocking(io.RawIOBase):
    def __init__(self, release):
        self._release = release

    def readable(self):
        return True

    def read(self, size=-1):
        self._release.wait(10)
        return b''


def _wait_gone(path, timeout=5.0):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and path.exists():
        time.sleep(0.02)
    return not path.exists()


def test_blocked_share_read_stalls_out_without_holding_the_job_thread(tmp_path, monkeypatch):
    """Conc C2: overlay copies run on an abandonable worker (COPY_STALL_SECONDS)."""
    import threading
    import time
    from sethlans_worker_agent.project_archive.errors import ShareStallError
    src = _overlay_source(tmp_path)
    cache = h.make_cache(tmp_path)
    release = threading.Event()
    real_open = open
    monkeypatch.setattr(overlay, 'open', lambda path, mode='r', *a, **k: _Blocking(release)
                        if str(path).startswith(str(src)) else real_open(path, mode, *a, **k), raising=False)
    started = time.monotonic()
    try:
        with pytest.raises(ShareStallError):
            _stage(cache, src, h.spec_digest(src), stall_seconds=0.3, poll_seconds=0.02)
        assert time.monotonic() - started < 3.0
        assert cache.ledger.in_flight_reserved == 0
    finally:
        release.set()
    assert _wait_gone(cache.layout.overlay_dir(42, 'abcdef01'))


def test_cancel_during_overlay_copy(tmp_path, monkeypatch):
    import threading
    from sethlans_worker_agent.project_archive.errors import AbortedError
    src = _overlay_source(tmp_path)
    cache = h.make_cache(tmp_path)
    release, cancel = threading.Event(), threading.Event()
    real_open = open
    monkeypatch.setattr(overlay, 'open', lambda path, mode='r', *a, **k: _Blocking(release)
                        if str(path).startswith(str(src)) else real_open(path, mode, *a, **k), raising=False)
    cancel.set()
    try:
        with pytest.raises(AbortedError):
            overlay.stage_overlay(str(src), cache.layout.overlay_dir(42, 'abcdef01'), h.spec_digest(src),
                                  Abort(cancel=cancel), reserve=lambda n: eviction.reserve(cache, n),
                                  share_validator=lambda p: str(p), digest_fn=h.spec_digest, poll_seconds=0.02)
    finally:
        release.set()
    assert _wait_gone(cache.layout.overlay_dir(42, 'abcdef01'))
