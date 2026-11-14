# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: ``manage.py unreal_fingerprint`` (Unreal foundation
FR-MS-4, D3, FR-T-3, AC-34).
"""

import hashlib
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from shared.unreal.overlay_digest import compute_overlay_digest
from workers.management.commands import unreal_fingerprint as cmd

from .unreal_helpers import set_share


@pytest.fixture
def share(tmp_path):
    root = tmp_path / 'share'
    (root / 'incoming').mkdir(parents=True)
    (root / 'incoming' / 'scene.zip').write_bytes(b'PK\x05\x06' + b'\x00' * 18 + b'payload')
    (root / 'fixes' / 'Config').mkdir(parents=True)
    (root / 'fixes' / 'Config' / 'DefaultEngine.ini').write_bytes(b'[x]\n')
    return root


def _run(relpath, share):
    out = StringIO()
    resolver = lambda rel: share / Path(rel)  # noqa: E731
    call_command('unreal_fingerprint', relpath, path_resolver=resolver, stdout=out)
    return out.getvalue().splitlines()


@pytest.mark.django_db
class TestFingerprint:

    def test_zip_prints_the_sha256_and_the_reminder(self, share):
        data = (share / 'incoming' / 'scene.zip').read_bytes()
        assert _run('incoming/scene.zip', share) == [
            f"archive_sha256={hashlib.sha256(data).hexdigest()}",
            cmd.ATTESTATION_REMINDER,
        ]

    def test_uppercase_zip_suffix_is_a_file(self, share):
        (share / 'incoming' / 'scene.zip').rename(share / 'incoming' / 'SCENE.ZIP')
        lines = _run(r'incoming\SCENE.ZIP', share)
        assert lines[0].startswith('archive_sha256=')

    def test_directory_prints_the_overlay_digest(self, share):
        expected = compute_overlay_digest(share / 'fixes').digest
        assert _run('fixes', share) == [f"overlay_sha256={expected}", cmd.ATTESTATION_REMINDER]

    def test_files_are_not_modified(self, share):
        target = share / 'incoming' / 'scene.zip'
        before = (target.read_bytes(), target.stat().st_mtime_ns)
        _run('incoming/scene.zip', share)
        assert (target.read_bytes(), target.stat().st_mtime_ns) == before

    @pytest.mark.parametrize('relpath', ['../x.zip', '/abs/x.zip', 'C:\\x.zip', 'a//b'])
    def test_invalid_path(self, share, relpath):
        with pytest.raises(CommandError, match='Invalid path'):
            _run(relpath, share)

    @pytest.mark.parametrize('relpath', ['incoming/missing.zip', 'missing-dir', 'incoming/scene.zip/x'])
    def test_missing_target(self, share, relpath):
        with pytest.raises(CommandError):
            _run(relpath, share)

    def test_unset_share_with_the_default_resolver(self):
        set_share('')
        with pytest.raises(CommandError, match='not configured'):
            call_command('unreal_fingerprint', 'incoming/scene.zip', stdout=StringIO())

    def test_default_resolver_uses_the_configured_share(self):
        set_share(r'\\nas.lan\sethlans')
        assert cmd.default_path_resolver('incoming/scene.zip') == Path(r'\\nas.lan\sethlans\incoming\scene.zip')

    def test_default_resolver_is_the_production_default(self, monkeypatch):
        seen = []
        monkeypatch.setattr(cmd, 'default_path_resolver', lambda rel: seen.append(rel) or Path('nowhere'))
        with pytest.raises(CommandError):
            call_command('unreal_fingerprint', 'fixes', stdout=StringIO())
        assert seen == ['fixes']
