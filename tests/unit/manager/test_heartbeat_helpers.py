# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for heartbeat view helpers in manager/workers/views/heartbeat.py.

Covers _validate_worker_status (AC-11), _sanitize_cpu_name (AC-12),
and _extract_gpu_name (AC-13).
"""

from workers.constants import WorkerStatus
from workers.views.heartbeat import (
    WorkerHeartbeatViewSet,
    _sanitize_cpu_name,
    _validate_worker_status,
)


class TestValidateWorkerStatus:
    """Tests for _validate_worker_status() — AC-11."""

    def test_idle_accepted(self):
        assert _validate_worker_status(WorkerStatus.IDLE) == WorkerStatus.IDLE

    def test_rendering_accepted(self):
        assert _validate_worker_status(WorkerStatus.RENDERING) == WorkerStatus.RENDERING

    def test_offline_rejected_defaults_to_idle(self):
        """OFFLINE is manager-derived, not accepted from workers."""
        assert _validate_worker_status(WorkerStatus.OFFLINE) == WorkerStatus.IDLE

    def test_bogus_string_defaults_to_idle(self):
        """AC-11: Invalid status value 'BOGUS' defaults to IDLE."""
        assert _validate_worker_status('BOGUS') == WorkerStatus.IDLE

    def test_empty_string_defaults_to_idle(self):
        assert _validate_worker_status('') == WorkerStatus.IDLE

    def test_none_defaults_to_idle(self):
        assert _validate_worker_status(None) == WorkerStatus.IDLE

    def test_integer_defaults_to_idle(self):
        assert _validate_worker_status(42) == WorkerStatus.IDLE

    def test_lowercase_idle_defaults_to_idle(self):
        """Case-sensitive: 'idle' is not 'IDLE'."""
        assert _validate_worker_status('idle') == WorkerStatus.IDLE

    def test_idle_string_literal_accepted(self):
        """Plain string 'IDLE' matches because WorkerStatus.IDLE == 'IDLE'."""
        assert _validate_worker_status('IDLE') == WorkerStatus.IDLE

    def test_rendering_string_literal_accepted(self):
        assert _validate_worker_status('RENDERING') == WorkerStatus.RENDERING


class TestSanitizeCpuName:
    """Tests for _sanitize_cpu_name() — AC-12."""

    def test_valid_cpu_name(self):
        assert _sanitize_cpu_name('Intel(R) Core(TM) i7-12700H') == \
            'Intel(R) Core(TM) i7-12700H'

    def test_valid_amd_cpu_name(self):
        assert _sanitize_cpu_name('AMD Ryzen 9 5950X 16-Core Processor') == \
            'AMD Ryzen 9 5950X 16-Core Processor'

    def test_valid_apple_silicon_name(self):
        assert _sanitize_cpu_name('Apple M1 Pro') == 'Apple M1 Pro'

    def test_valid_with_at_sign(self):
        assert _sanitize_cpu_name('Intel Xeon E5-2680 v4 @ 2.40GHz') == \
            'Intel Xeon E5-2680 v4 @ 2.40GHz'

    def test_valid_with_comma(self):
        name = 'Intel64 Family 6 Model 154 Stepping 3, GenuineIntel'
        assert _sanitize_cpu_name(name) == name

    def test_valid_with_forward_slash(self):
        assert _sanitize_cpu_name('ARM/v8') == 'ARM/v8'

    def test_valid_with_hash_and_plus(self):
        assert _sanitize_cpu_name('CPU #1 C++') == 'CPU #1 C++'

    def test_script_tag_rejected(self):
        """AC-12: XSS script tag is sanitized to empty string."""
        assert _sanitize_cpu_name('<script>alert(1)</script>') == ''

    def test_html_tag_rejected(self):
        assert _sanitize_cpu_name('<b>bold</b>') == ''

    def test_angle_brackets_rejected(self):
        assert _sanitize_cpu_name('CPU < 3GHz') == ''

    def test_semicolon_rejected(self):
        assert _sanitize_cpu_name('CPU; DROP TABLE') == ''

    def test_backtick_rejected(self):
        assert _sanitize_cpu_name('CPU `uname -a`') == ''

    def test_curly_braces_rejected(self):
        assert _sanitize_cpu_name('CPU {evil}') == ''

    def test_non_string_input_returns_empty(self):
        assert _sanitize_cpu_name(None) == ''
        assert _sanitize_cpu_name(123) == ''
        assert _sanitize_cpu_name(['list']) == ''

    def test_empty_string_accepted(self):
        assert _sanitize_cpu_name('') == ''

    def test_whitespace_only_accepted(self):
        """Whitespace chars are in the allowed set."""
        assert _sanitize_cpu_name('   ') == '   '


class TestExtractGpuName:
    """Tests for _extract_gpu_name() — AC-13."""

    def test_single_gpu(self):
        tools = {
            'gpu_devices_details': [
                {'name': 'NVIDIA RTX 4090', 'type': 'CUDA'},
            ],
        }
        assert WorkerHeartbeatViewSet._extract_gpu_name(tools) == 'NVIDIA RTX 4090'

    def test_multiple_gpus(self):
        tools = {
            'gpu_devices_details': [
                {'name': 'NVIDIA RTX 4090', 'type': 'CUDA'},
                {'name': 'NVIDIA RTX 3080', 'type': 'CUDA'},
            ],
        }
        result = WorkerHeartbeatViewSet._extract_gpu_name(tools)
        assert result == 'NVIDIA RTX 4090, NVIDIA RTX 3080'

    def test_empty_gpu_details_list(self):
        tools = {'gpu_devices_details': []}
        assert WorkerHeartbeatViewSet._extract_gpu_name(tools) == ''

    def test_missing_gpu_details_key(self):
        tools = {'blender': ['4.2.0']}
        assert WorkerHeartbeatViewSet._extract_gpu_name(tools) == ''

    def test_empty_dict(self):
        assert WorkerHeartbeatViewSet._extract_gpu_name({}) == ''

    def test_none_input(self):
        """AC-13: Non-dict argument returns '' without exception."""
        assert WorkerHeartbeatViewSet._extract_gpu_name(None) == ''

    def test_string_input(self):
        """AC-13: String argument returns '' without exception."""
        assert WorkerHeartbeatViewSet._extract_gpu_name('string') == ''

    def test_list_input(self):
        """AC-13: List argument returns '' without exception."""
        assert WorkerHeartbeatViewSet._extract_gpu_name([1, 2, 3]) == ''

    def test_integer_input(self):
        assert WorkerHeartbeatViewSet._extract_gpu_name(42) == ''

    def test_gpu_details_not_a_list(self):
        """AC-13: gpu_devices_details is a string instead of list."""
        tools = {'gpu_devices_details': 'not a list'}
        assert WorkerHeartbeatViewSet._extract_gpu_name(tools) == ''

    def test_gpu_details_entries_not_dicts(self):
        """AC-13: Entries in the list are not dicts."""
        tools = {'gpu_devices_details': ['string', 42, None]}
        assert WorkerHeartbeatViewSet._extract_gpu_name(tools) == ''

    def test_gpu_entry_missing_name_key(self):
        tools = {'gpu_devices_details': [{'type': 'CUDA'}]}
        assert WorkerHeartbeatViewSet._extract_gpu_name(tools) == ''

    def test_gpu_entry_with_empty_name(self):
        tools = {'gpu_devices_details': [{'name': '', 'type': 'CUDA'}]}
        assert WorkerHeartbeatViewSet._extract_gpu_name(tools) == ''

    def test_mixed_valid_and_invalid_entries(self):
        tools = {
            'gpu_devices_details': [
                {'name': 'NVIDIA RTX 4090', 'type': 'CUDA'},
                'not_a_dict',
                {'type': 'HIP'},  # missing name
                {'name': 'AMD Radeon RX 7900', 'type': 'HIP'},
            ],
        }
        result = WorkerHeartbeatViewSet._extract_gpu_name(tools)
        assert result == 'NVIDIA RTX 4090, AMD Radeon RX 7900'
