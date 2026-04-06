# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for video-related constants.
"""

from workers.constants import (
    VideoStatus,
    VIDEO_PRESETS, VIDEO_CODECS, VIDEO_CONTAINERS,
    VIDEO_CODEC_CONTAINER_MAP, CONTAINER_CONTENT_TYPES,
    VIDEO_COMPATIBLE_FORMATS,
)


class TestVideoStatus:
    def test_has_expected_values(self):
        assert VideoStatus.PENDING == 'PENDING'
        assert VideoStatus.ASSEMBLING == 'ASSEMBLING'
        assert VideoStatus.DONE == 'DONE'
        assert VideoStatus.ERROR == 'ERROR'

    def test_choices_count(self):
        assert len(VideoStatus.choices) == 4


class TestVideoPresets:
    def test_web_h264_preset(self):
        p = VIDEO_PRESETS['web_h264']
        assert p['container'] == 'mp4'
        assert p['codec'] == 'libx264'
        assert p['crf'] == 23

    def test_hq_h265_preset(self):
        p = VIDEO_PRESETS['hq_h265']
        assert p['codec'] == 'libx265'

    def test_web_vp9_preset(self):
        p = VIDEO_PRESETS['web_vp9']
        assert p['container'] == 'webm'
        assert p['codec'] == 'libvpx-vp9'

    def test_archive_prores_preset(self):
        p = VIDEO_PRESETS['archive_prores']
        assert p['container'] == 'mov'
        assert p['codec'] == 'prores_ks'
        assert p['crf'] == 0


class TestVideoCodecsAndContainers:
    def test_codecs_are_frozenset(self):
        assert isinstance(VIDEO_CODECS, frozenset)
        assert 'libx264' in VIDEO_CODECS

    def test_containers_are_frozenset(self):
        assert isinstance(VIDEO_CONTAINERS, frozenset)
        assert 'mp4' in VIDEO_CONTAINERS

    def test_codec_container_map_consistency(self):
        for codec, containers in VIDEO_CODEC_CONTAINER_MAP.items():
            assert codec in VIDEO_CODECS
            for c in containers:
                assert c in VIDEO_CONTAINERS

    def test_content_types(self):
        assert CONTAINER_CONTENT_TYPES['mp4'] == 'video/mp4'
        assert CONTAINER_CONTENT_TYPES['webm'] == 'video/webm'
        assert CONTAINER_CONTENT_TYPES['mov'] == 'video/quicktime'


class TestVideoCompatibleFormats:
    def test_excludes_hdr_formats(self):
        assert 'OPEN_EXR' not in VIDEO_COMPATIBLE_FORMATS
        assert 'OPEN_EXR_MULTILAYER' not in VIDEO_COMPATIBLE_FORMATS
        assert 'HDR' not in VIDEO_COMPATIBLE_FORMATS

    def test_includes_standard_formats(self):
        assert 'PNG' in VIDEO_COMPATIBLE_FORMATS
        assert 'JPEG' in VIDEO_COMPATIBLE_FORMATS
        assert 'TIFF' in VIDEO_COMPATIBLE_FORMATS
