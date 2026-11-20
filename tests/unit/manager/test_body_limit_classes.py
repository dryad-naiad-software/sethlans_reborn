# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the request size class table of
:mod:`sethlans_manager.body_limits` (Unreal sequence spec D21, FR-BL-1,
FR-BL-2, FR-BL-4).

Covers:

- The D21 sizes and the class table's validation.
- The live table has the upload class, the frame class (``frames/``) and
  the result class with every D21 result path (chunk 3); ``sound-result/``
  still gets the small limit, and the static Docker Caddyfile carries the
  live classes.
- With the D21 table installed: ``body_limit_for_path`` for every
  sequence path and near-miss paths, the listener ceiling, and that no
  two classes share a path.
- The Caddyfile text generated from the table: one matcher and one
  ``request_body`` per class with its exact size, a small matcher that
  excludes every class's paths, a loopback vhost that stays small; the
  static Docker Caddyfile names exactly the live table's classes.
"""

from __future__ import annotations

import re

import pytest

from sethlans_manager import body_limits
from sethlans_manager.body_limits import (
    FRAME_BODY_LIMIT,
    FRAME_FILE_CAP,
    RESULT_BODY_LIMIT,
    SMALL_BODY_LIMIT,
    SOUND_BODY_LIMIT,
    SOUND_FILE_CAP,
    UPLOAD_BODY_LIMIT,
    UPLOAD_CLASS,
    UPLOAD_HEADROOM,
    UPLOAD_PATHS,
    BodyClass,
    body_class_for_path,
    body_limit_for_path,
    check_disjoint,
)
from sethlans_manager.caddy_body_classes import body_limit_directives
from sethlans_manager.waitress_body_limits import class_probe_path
from tests._body_limit_classes import (
    D21_CLASSES,
    FRAME_CLASS,
    RESULT_CLASS,
    SOUND_CLASS,
    use_d21_classes,
)
from tests.unit.manager.test_body_limits import REPO_ROOT, _docker_caddyfile_text

MIB = 1024 * 1024
GIB = 1024 * MIB

SEQUENCE_PATHS = [
    ('/api/jobs/7/frames/', FRAME_BODY_LIMIT),
    ('/api/jobs/2147483647/frames/', FRAME_BODY_LIMIT),
    ('/api/jobs/7/sound-result/', SOUND_BODY_LIMIT),
    ('/api/jobs/7/shots-result/', RESULT_BODY_LIMIT),
    ('/api/jobs/7/piece-result/', RESULT_BODY_LIMIT),
    ('/api/sequence-submissions/', RESULT_BODY_LIMIT),
]

NEAR_MISSES = [
    '/api/jobs/frames/',
    '/api/jobs/7/frames',
    '/api/jobs/7/frames/extra/',
    '/api/jobs/7/8/frames/',
    '/api/jobs/7/sound-result',
    '/api/jobs/7/sound/',
    '/api/jobs/7/shots-results/',
    '/api/jobs/7/pilot-result/',
    '/api/jobs/7/piece-result/x',
    '/api/sequence-submissions/5/',
    '/api/sequence-submissions',
    '/api/sequence-discoveries/',
    '/API/JOBS/7/FRAMES/',
]


@pytest.fixture
def d21(monkeypatch):
    return use_d21_classes(monkeypatch)


class TestSizes:
    def test_d21_sizes(self):
        assert FRAME_BODY_LIMIT == 514 * MIB
        assert SOUND_BODY_LIMIT == 4 * GIB + 2 * MIB
        assert RESULT_BODY_LIMIT == 1 * MIB

    def test_file_classes_have_the_upload_headroom(self):
        assert FRAME_FILE_CAP == 512 * MIB
        assert SOUND_FILE_CAP == 4 * GIB
        assert FRAME_BODY_LIMIT - FRAME_FILE_CAP == UPLOAD_HEADROOM
        assert SOUND_BODY_LIMIT - SOUND_FILE_CAP == UPLOAD_HEADROOM

    def test_every_class_sits_between_small_and_upload(self):
        for body_class in (FRAME_CLASS, SOUND_CLASS, RESULT_CLASS):
            assert SMALL_BODY_LIMIT < body_class.limit < UPLOAD_BODY_LIMIT


LIVE_PATHS = [
    ('/api/jobs/7/frames/', FRAME_BODY_LIMIT),
    ('/api/jobs/2147483647/frames/', FRAME_BODY_LIMIT),
    ('/api/jobs/7/shots-result/', RESULT_BODY_LIMIT),
    ('/api/jobs/7/piece-result/', RESULT_BODY_LIMIT),
    ('/api/sequence-submissions/', RESULT_BODY_LIMIT),
]


class TestLiveTable:
    """Chunk 3: the frame class (``frames/``) and the whole result class are
    live; ``sound-result/`` keeps the small limit until its endpoint lands."""

    def test_upload_frame_and_result_classes(self):
        assert body_limits.BODY_CLASSES == (UPLOAD_CLASS, body_limits.FRAME_CLASS, body_limits.RESULT_CLASS)
        assert UPLOAD_CLASS.paths is UPLOAD_PATHS
        assert UPLOAD_CLASS.limit == UPLOAD_BODY_LIMIT
        assert body_limits.RESULT_CLASS.name == RESULT_CLASS.name == 'result'
        assert body_limits.RESULT_CLASS.limit == RESULT_BODY_LIMIT
        assert body_limits.FRAME_CLASS == FRAME_CLASS

    def test_live_result_paths_equal_d21(self):
        assert set(body_limits.RESULT_PATHS) == set(RESULT_CLASS.paths)

    @pytest.mark.parametrize('path, limit', LIVE_PATHS)
    def test_live_sequence_path_gets_its_class(self, path, limit):
        assert body_class_for_path(path).limit == limit
        assert body_limit_for_path(path) == limit

    @pytest.mark.parametrize('path, _limit', [p for p in SEQUENCE_PATHS if p not in LIVE_PATHS])
    def test_other_sequence_paths_are_small_until_their_endpoints_land(self, path, _limit):
        assert body_class_for_path(path) is None
        assert body_limit_for_path(path) == SMALL_BODY_LIMIT

    @pytest.mark.parametrize('path', NEAR_MISSES)
    def test_near_miss_is_small(self, path):
        assert body_limit_for_path(path) == SMALL_BODY_LIMIT

    def test_static_caddyfile_carries_the_frame_and_result_classes(self):
        docker_text = _docker_caddyfile_text(REPO_ROOT)
        public = docker_text[:docker_text.index('http://127.0.0.1:')]
        assert '    @frame_body {\n        path /api/jobs/*/frames/\n    }\n' in public
        assert ('    @result_body {\n        path /api/jobs/*/shots-result/ /api/jobs/*/piece-result/ '
                '/api/sequence-submissions/\n    }\n') in public
        assert f'    request_body @frame_body {{\n        max_size {FRAME_BODY_LIMIT}\n    }}\n' in public
        assert f'    request_body @result_body {{\n        max_size {RESULT_BODY_LIMIT}\n    }}\n' in public
        small = public[public.index('@small_body {'):]
        for path in ('/api/jobs/*/frames/', '/api/jobs/*/piece-result/', '/api/sequence-submissions/'):
            assert path in small


class TestBodyClassValidation:
    @pytest.mark.parametrize('name', ['', 'small', 'Frame', 'sound_result', 'r1', 'a b'])
    def test_bad_name(self, name):
        with pytest.raises(ValueError, match='name'):
            BodyClass(name, MIB, ('/api/x/',))

    @pytest.mark.parametrize('limit', [0, -1, True, 1.5, '1'])
    def test_bad_limit(self, limit):
        with pytest.raises(ValueError, match='limit'):
            BodyClass('frame', limit, ('/api/x/',))

    @pytest.mark.parametrize('paths', [(), ('api/x/',), ('',), (None,)])
    def test_bad_paths(self, paths):
        with pytest.raises(ValueError, match='path patterns'):
            BodyClass('frame', MIB, paths)


class TestD21Classifier:
    @pytest.mark.parametrize('path, limit', SEQUENCE_PATHS)
    def test_sequence_path_gets_its_class(self, d21, path, limit):
        assert body_limit_for_path(path) == limit

    @pytest.mark.parametrize('path', NEAR_MISSES)
    def test_near_miss_is_small(self, d21, path):
        assert body_class_for_path(path) is None
        assert body_limit_for_path(path) == SMALL_BODY_LIMIT

    @pytest.mark.parametrize('path', ['/api/assets/', '/api/jobs/7/upload_output/'])
    def test_upload_paths_unchanged(self, d21, path):
        assert body_limit_for_path(path) == UPLOAD_BODY_LIMIT

    def test_ceiling_caps_every_class(self, d21):
        for path, _limit in SEQUENCE_PATHS:
            assert body_limit_for_path(path, upload_limit=1000) == 1000
        assert body_limit_for_path('/api/jobs/7/frames/', upload_limit=2 * MIB) == 2 * MIB
        assert body_limit_for_path('/api/jobs/7/shots-result/', upload_limit=2 * MIB) == RESULT_BODY_LIMIT


@pytest.mark.parametrize('table', [D21_CLASSES, body_limits.BODY_CLASSES], ids=['d21', 'live'])
class TestClassesDisjoint:
    """Caddy applies every matching limit, ``body_limit_for_path`` the first."""

    def test_names_unique(self, table):
        assert len({body_class.name for body_class in table}) == len(table)

    def test_each_pattern_matches_its_own_class_only(self, table):
        check_disjoint(table)
        for body_class in table:
            for pattern in body_class.paths:
                probe = pattern.replace('*', '1')
                assert [c.name for c in table if c.matches(probe)] == [body_class.name]

    def test_probe_path_is_in_its_class(self, table):
        for body_class in table:
            assert body_class.matches(class_probe_path(body_class))


class TestOverlapRefused:
    """Waitress takes the first match, Caddy applies every match: an overlap
    would make them disagree, so the table check refuses it."""

    @pytest.mark.parametrize('paths', [
        ('/api/jobs/*',),
        ('/api/jobs/*/frames/',),
        ('/api/jobs/1/sound-result/',),
        ('/api/assets/x/',),
    ])
    def test_overlapping_class_refused(self, paths):
        with pytest.raises(ValueError, match='overlaps'):
            check_disjoint(D21_CLASSES + (BodyClass('extra', MIB, paths),))

    def test_duplicate_name_refused(self):
        with pytest.raises(ValueError, match='unique'):
            check_disjoint(D21_CLASSES + (BodyClass('frame', MIB, ('/api/other/',)),))


class TestCaddyText:
    def test_one_matcher_and_limit_per_class(self, d21):
        text = body_limit_directives(allow_uploads=True)
        for body_class in D21_CLASSES:
            assert (
                f'    @{body_class.name}_body {{\n'
                f'        path {" ".join(body_class.paths)}\n    }}\n'
            ) in text
            assert (
                f'    request_body @{body_class.name}_body {{\n'
                f'        max_size {body_class.limit}\n    }}\n'
            ) in text
        assert text.count('request_body @') == len(D21_CLASSES) + 1

    def test_exact_sizes_in_the_text(self, d21):
        text = body_limit_directives(allow_uploads=True)
        sizes = [int(n) for n in re.findall(r'max_size (\d+)', text)]
        assert sizes == [UPLOAD_BODY_LIMIT, 514 * MIB, 4 * GIB + 2 * MIB, MIB, SMALL_BODY_LIMIT]

    def test_small_matcher_excludes_every_class_path(self, d21):
        text = body_limit_directives(allow_uploads=True)
        every = ' '.join(p for body_class in D21_CLASSES for p in body_class.paths)
        assert f'    @small_body {{\n        not path {every}\n    }}\n' in text
        assert text.index('@small_body {') > text.index('@result_body {')

    def test_loopback_stays_small(self, d21):
        text = body_limit_directives(allow_uploads=False)
        assert text == f'    request_body {{\n        max_size {SMALL_BODY_LIMIT}\n    }}\n'

    def test_rendered_caddyfile_carries_the_table(self, d21, tmp_path):
        from sethlans_manager.caddy_template import render_manager_caddyfile
        (tmp_path / 'tls').mkdir()
        cert, key = tmp_path / 'tls' / 'm.crt', tmp_path / 'tls' / 'm.key'
        cert.write_text('CERT')
        key.write_text('KEY')
        text = render_manager_caddyfile(
            public_tls_port=8080, loopback_plaintext_port=8089,
            waitress_public_port=18080, waitress_internal_port=18088,
            cert_path=cert, key_path=key, manager_data_dir=tmp_path,
        )
        public, loopback = text.split('http://127.0.0.1:8089 {')
        assert body_limit_directives(allow_uploads=True) in public
        assert f'max_size {SOUND_BODY_LIMIT}' in public
        assert '@sound_body' not in loopback

    def test_static_caddyfile_names_the_live_classes_only(self):
        docker_text = _docker_caddyfile_text(REPO_ROOT)
        public = docker_text[:docker_text.index('http://127.0.0.1:')]
        named = re.findall(r'request_body @(\w+)_body', public)
        assert named == [c.name for c in body_limits.BODY_CLASSES] + ['small']
        for body_class in body_limits.BODY_CLASSES:
            assert f'max_size {body_class.limit}\n' in public
