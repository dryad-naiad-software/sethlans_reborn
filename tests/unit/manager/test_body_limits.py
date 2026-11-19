# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for :mod:`sethlans_manager.body_limits` and the request body
limits in the Caddyfiles (issue #260).

Covers:

- Which paths are upload paths (Caddy ``path`` matcher semantics:
  trailing ``*`` is a prefix, an inner ``*`` is one segment).
- The limit chosen per path, and that the limits cover the largest
  bodies the manager's own validators accept.
- The rendered Caddyfile carries the per-route ``request_body``
  limits on the public vhost and a small limit on the loopback vhost.
- The static Docker Caddyfile carries the same directives. That check is
  skipped when ``deploy/`` is absent (the Docker test image does not copy
  it, #275) and fails when ``deploy/`` exists without the Caddyfile.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sethlans_manager import body_limits
from sethlans_manager.body_limits import (
    SMALL_BODY_LIMIT,
    UPLOAD_BODY_LIMIT,
    body_limit_for_path,
    is_upload_path,
)
from sethlans_manager.caddy_template import (
    body_limit_directives,
    render_manager_caddyfile,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKER_CADDYFILE = ('deploy', 'docker', 'manager', 'Caddyfile')
NO_DEPLOY_REASON = (
    'deploy/ is absent (the Docker test image does not copy it); '
    'the Docker Caddyfile parity check needs the full repository tree'
)


def _docker_caddyfile(root):
    """The Docker Caddyfile under ``root``, or ``None`` when ``deploy/`` is absent (#275).

    When ``deploy/`` exists the Caddyfile must exist too.
    """
    if not (root / 'deploy').is_dir():
        return None
    path = root.joinpath(*DOCKER_CADDYFILE)
    assert path.is_file(), f'{path} is missing although deploy/ exists'
    return path


class TestUploadPathClassifier:
    @pytest.mark.parametrize('path', [
        '/api/assets/',
        '/api/assets/12/',
        '/api/jobs/7/upload_output/',
        '/api/jobs/2147483647/upload_output/',
        '/admin/workers/asset/add/',
        '/admin/workers/asset/3/change/',
    ])
    def test_upload_paths(self, path):
        assert is_upload_path(path)
        assert body_limit_for_path(path) == UPLOAD_BODY_LIMIT

    @pytest.mark.parametrize('path', [
        '/api/enroll/',
        '/api/heartbeat/',
        '/api/heartbeat/4/',
        '/api/jobs/',
        '/api/jobs/7/claim/',
        '/api/jobs/7/pilot-result/',
        '/api/jobs/7/upload_output/extra/',
        '/api/jobs/7/8/upload_output/',
        '/api/assets',
        '/API/ASSETS/',
        '/api/setup/database/',
        '/admin/login/',
        '/admin/workers/job/add/',
        '/',
        '',
    ])
    def test_small_paths(self, path):
        assert not is_upload_path(path)
        assert body_limit_for_path(path) == SMALL_BODY_LIMIT

    def test_small_limit_never_above_a_lower_ceiling(self):
        assert body_limit_for_path('/api/enroll/', upload_limit=1000) == 1000
        assert body_limit_for_path('/api/assets/', upload_limit=1000) == 1000


class TestLimitSizes:
    def test_small_limit_covers_manager_json_caps(self):
        """The small limit must exceed every JSON cap the validators accept."""
        from workers.serializers.unreal_measurement import MAX_SUMMARY_BYTES
        from workers.views.heartbeat_validators import MAX_AVAILABLE_TOOLS_BYTES
        assert SMALL_BODY_LIMIT == 128 * 1024
        # available_tools at its cap plus the rest of a heartbeat at
        # every cap (about 37 KB measured, cards report included).
        assert SMALL_BODY_LIMIT > MAX_AVAILABLE_TOOLS_BYTES + 40_000
        assert SMALL_BODY_LIMIT > 2 * MAX_SUMMARY_BYTES

    def test_upload_limit_has_headroom_over_serializer_caps(self):
        """A file the serializer accepts must fit with its multipart envelope."""
        from workers.serializers.assets import MAX_BLEND_FILE_SIZE
        from workers.views.job_output_upload import MAX_THUMBNAIL_SIZE
        from workers.views.upload_helpers import MAX_UPLOAD_SIZE
        mib = 1024 * 1024
        assert UPLOAD_BODY_LIMIT >= MAX_BLEND_FILE_SIZE + mib
        # Output upload: the file, its thumbnail and the envelope.
        assert UPLOAD_BODY_LIMIT >= MAX_UPLOAD_SIZE + MAX_THUMBNAIL_SIZE + 64 * 1024

    def test_waitress_default_is_the_upload_limit(self):
        from sethlans_manager.waitress_config import DEFAULT_MAX_REQUEST_BODY_SIZE
        assert DEFAULT_MAX_REQUEST_BODY_SIZE == UPLOAD_BODY_LIMIT

    def test_django_setting_is_the_small_limit(self):
        from django.conf import settings
        assert settings.DATA_UPLOAD_MAX_MEMORY_SIZE == SMALL_BODY_LIMIT


@pytest.fixture
def rendered(tmp_path):
    data_dir = tmp_path / 'manager_data'
    (data_dir / 'tls').mkdir(parents=True)
    cert = data_dir / 'tls' / 'manager.crt'
    key = data_dir / 'tls' / 'manager.key'
    cert.write_text('CERT')
    key.write_text('KEY')
    return render_manager_caddyfile(
        public_tls_port=8080, loopback_plaintext_port=8089,
        waitress_public_port=18080, waitress_internal_port=18088,
        cert_path=cert, key_path=key, manager_data_dir=data_dir,
    )


def _site_blocks(text):
    """Split a Caddyfile into (public vhost, loopback vhost) text."""
    public_start = text.index(':8080 {')
    loopback_start = text.index('http://127.0.0.1:8089 {')
    return text[public_start:loopback_start], text[loopback_start:]


class TestCaddyfileBodyLimits:
    def test_public_vhost_has_per_route_limits(self, rendered):
        public, _ = _site_blocks(rendered)
        paths = ' '.join(body_limits.UPLOAD_PATHS)
        assert f'    @upload_body {{\n        path {paths}\n    }}\n' in public
        assert f'    @small_body {{\n        not path {paths}\n    }}\n' in public
        assert (
            '    request_body @upload_body {\n'
            f'        max_size {UPLOAD_BODY_LIMIT}\n    }}\n'
        ) in public
        assert (
            '    request_body @small_body {\n'
            f'        max_size {SMALL_BODY_LIMIT}\n    }}\n'
        ) in public

    def test_limits_come_before_the_proxy(self, rendered):
        public, loopback = _site_blocks(rendered)
        assert public.index('request_body') < public.index('reverse_proxy')
        assert loopback.index('request_body') < loopback.index('reverse_proxy')

    def test_loopback_vhost_is_small_on_every_path(self, rendered):
        _, loopback = _site_blocks(rendered)
        assert (
            f'    request_body {{\n        max_size {SMALL_BODY_LIMIT}\n    }}\n'
        ) in loopback
        assert '@upload_body' not in loopback
        assert str(UPLOAD_BODY_LIMIT) not in loopback

    def test_every_upload_pattern_is_listed(self, rendered):
        for pattern in body_limits.UPLOAD_PATHS:
            assert pattern in rendered


def _docker_caddyfile_text(root):
    """The Docker Caddyfile's text; skips the calling test when ``deploy/`` is absent."""
    path = _docker_caddyfile(root)
    if path is None:
        pytest.skip(NO_DEPLOY_REASON)
    return path.read_text(encoding='utf-8').replace('\r\n', '\n')


@pytest.fixture
def docker_text():
    return _docker_caddyfile_text(REPO_ROOT)


class TestDockerCaddyfileParity:
    """The static Docker Caddyfile must carry the template's limits."""

    def test_public_vhost_block_matches_template(self, docker_text):
        public = docker_text[:docker_text.index('http://127.0.0.1:')]
        assert body_limit_directives(allow_uploads=True) in public

    def test_loopback_vhost_block_matches_template(self, docker_text):
        loopback = docker_text[docker_text.index('http://127.0.0.1:'):]
        assert body_limit_directives(allow_uploads=False) in loopback


class TestDockerCaddyfileLookup:
    """``deploy/`` is optional; when it exists the Caddyfile is required."""

    def test_found_when_deploy_has_it(self, tmp_path):
        path = tmp_path.joinpath(*DOCKER_CADDYFILE)
        path.parent.mkdir(parents=True)
        path.write_text(':8080 {\n}\n')
        assert _docker_caddyfile(tmp_path) == path

    def test_none_without_deploy(self, tmp_path):
        assert _docker_caddyfile(tmp_path) is None

    def test_fails_when_deploy_lacks_the_caddyfile(self, tmp_path):
        (tmp_path / 'deploy' / 'docker').mkdir(parents=True)
        with pytest.raises(AssertionError, match='missing although deploy/ exists'):
            _docker_caddyfile(tmp_path)

    def test_parity_tests_skip_without_deploy(self, tmp_path):
        with pytest.raises(pytest.skip.Exception, match='deploy/ is absent'):
            _docker_caddyfile_text(tmp_path)
