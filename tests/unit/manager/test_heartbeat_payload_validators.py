# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the self-reported heartbeat field validators added with
issue #215: ``_validate_ip_address``, ``_sanitize_os`` and
``_validate_available_tools`` in ``workers.views.heartbeat_validators``.
"""

import pytest

from workers.views.heartbeat_validators import (
    MAX_AVAILABLE_TOOLS_BYTES,
    MAX_OS_LENGTH,
    _sanitize_os,
    _validate_available_tools,
    _validate_ip_address,
)


class TestValidateIpAddress:

    @pytest.mark.parametrize('value', ['10.1.2.3', '::1', 'fe80::1'])
    def test_valid_addresses_accepted(self, value):
        assert _validate_ip_address(value) == value

    def test_ipv6_is_normalised(self):
        assert _validate_ip_address('2001:0db8:0000::0001') == '2001:db8::1'

    @pytest.mark.parametrize('value', [
        'not-an-ip', '999.1.1.1', '10.0.0.1/24', '', None, 12345,
        ['10.0.0.1'], {'ip': '10.0.0.1'}, '10.0.0.1\nINJECT',
    ])
    def test_invalid_values_become_none(self, value):
        assert _validate_ip_address(value) is None


class TestSanitizeOs:

    def test_plain_string_kept(self):
        assert _sanitize_os('Linux 6.8') == 'Linux 6.8'

    def test_none_becomes_empty(self):
        assert _sanitize_os(None) == ''

    def test_truncated_to_column_width(self):
        assert _sanitize_os('x' * 500) == 'x' * MAX_OS_LENGTH

    def test_non_string_is_stringified_and_truncated(self):
        result = _sanitize_os({'name': 'y' * 500})
        assert isinstance(result, str)
        assert len(result) == MAX_OS_LENGTH


class TestValidateAvailableTools:

    def test_dict_within_cap_kept(self):
        tools = {'blender': ['4.2.19'], 'gpu_devices_details': []}
        assert _validate_available_tools(tools) is tools

    @pytest.mark.parametrize('value', [
        None, 'blender', ['4.2.19'], 42, True,
    ])
    def test_non_dict_becomes_empty(self, value):
        assert _validate_available_tools(value) == {}

    def test_oversized_payload_ignored(self):
        tools = {'blender': ['x' * MAX_AVAILABLE_TOOLS_BYTES]}
        assert _validate_available_tools(tools) == {}

    def test_payload_just_under_cap_kept(self):
        # '{"k":"' + body + '"}' is 8 bytes of overhead.
        tools = {'k': 'x' * (MAX_AVAILABLE_TOOLS_BYTES - 8)}
        assert _validate_available_tools(tools) is tools
