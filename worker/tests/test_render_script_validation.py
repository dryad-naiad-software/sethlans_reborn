# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for input validation and command injection prevention
in the render_script module (GitHub issue #4).
"""

import pytest

from sethlans_worker_agent import render_script
from sethlans_worker_agent.render_script import _VALID_ENGINES


@pytest.fixture
def mock_gpu(mocker):
    """Mock GPU detection to return no GPUs (simplifies CPU-path tests)."""
    mocker.patch(
        "sethlans_worker_agent.system_monitor.detect_gpu_devices",
        return_value=[],
    )


# ------------------------------------------------------------------ #
# _SAFE_KEY_RE pattern tests
# ------------------------------------------------------------------ #

class TestSafeKeyRegex:
    """Direct tests of the _SAFE_KEY_RE validation pattern."""

    @pytest.mark.parametrize("key", [
        "render.resolution_x",
        "render.resolution_y",
        "cycles.samples",
        "render.film_transparent",
        "eevee.taa_render_samples",
        "output.file_format",
        "simple",
        "_private",
        "a",
    ])
    def test_accepts_valid_python_attribute_paths(self, key):
        assert render_script._SAFE_KEY_RE.match(key) is not None

    @pytest.mark.parametrize("key", [
        "render.resolution_x; import os",
        "x\nimport os",
        "render.resolution_x\nos.system('rm -rf /')",
        "__import__('os').system('whoami')",
        "x = 1; exec('bad')",
        "render[0]",
        "render['x']",
        "x + y",
        "x()",
        "",
        " ",
        "123starts_with_digit",
        ".leading_dot",
        "key with spaces",
        "key\ttab",
        "key;semicolon",
        "key#comment",
        "key=value",
    ])
    def test_rejects_dangerous_keys(self, key):
        assert render_script._SAFE_KEY_RE.match(key) is None


# ------------------------------------------------------------------ #
# Script generation: attack paths (GitHub issue #4)
# ------------------------------------------------------------------ #

class TestCommandInjectionPrevention:
    """Verify that malicious keys are filtered out of the generated script."""

    def test_semicolon_injection_is_skipped(self, mock_gpu):
        settings = {"render.resolution_x; import os": 1920}
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine="CYCLES",
            render_device="CPU",
            render_settings=settings,
        )

        assert "import os" not in script
        assert "resolution_x" not in script

    def test_newline_injection_is_skipped(self, mock_gpu):
        settings = {"x\nimport os": 1}
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine="CYCLES",
            render_device="CPU",
            render_settings=settings,
        )

        assert "import os" not in script

    def test_exec_call_injection_is_skipped(self, mock_gpu):
        settings = {"x = 1; exec('bad')": 1}
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine="CYCLES",
            render_device="CPU",
            render_settings=settings,
        )

        assert "exec" not in script

    def test_dunder_import_injection_is_skipped(self, mock_gpu):
        settings = {"__import__('os').system('whoami')": 1}
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine="CYCLES",
            render_device="CPU",
            render_settings=settings,
        )

        assert "__import__" not in script
        assert "system" not in script

    def test_valid_keys_survive_alongside_invalid_ones(self, mock_gpu):
        """Valid settings must still be applied even when bad keys exist."""
        settings = {
            "render.resolution_x": 1920,
            "bad;key": 999,
            "cycles.samples": 64,
        }
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine="CYCLES",
            render_device="CPU",
            render_settings=settings,
        )

        assert "scene.render.resolution_x = 1920" in script
        assert "scene.cycles.samples = 64" in script
        assert "bad" not in script
        assert "999" not in script

    def test_bracket_access_injection_is_skipped(self, mock_gpu):
        settings = {"render['x']": 1}
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine="CYCLES",
            render_device="CPU",
            render_settings=settings,
        )

        assert "render['x']" not in script

    def test_parenthesis_call_injection_is_skipped(self, mock_gpu):
        settings = {"os.system('ls')": 1}
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine="CYCLES",
            render_device="CPU",
            render_settings=settings,
        )

        # The key "os.system('ls')" contains parens and quotes, so it
        # should be rejected. "os.system" alone would pass the regex,
        # but the full key with parens won't.
        assert "('ls')" not in script


# ------------------------------------------------------------------ #
# Render engine allowlist validation
# ------------------------------------------------------------------ #

class TestRenderEngineAllowlist:
    """Verify that the _VALID_ENGINES allowlist is enforced."""

    def test_invalid_engine_raises_value_error(self, mock_gpu):
        with pytest.raises(ValueError, match="FAKE_ENGINE"):
            render_script.generate_render_config_script(
                job_id=1,
                render_engine="FAKE_ENGINE",
                render_device="CPU",
                render_settings=None,
            )

    @pytest.mark.parametrize("engine", sorted(_VALID_ENGINES))
    def test_each_valid_engine_is_accepted(self, engine, mock_gpu):
        # Should not raise
        script = render_script.generate_render_config_script(
            job_id=1,
            render_engine=engine,
            render_device="CPU",
            render_settings=None,
        )
        assert f"render.engine = '{engine}'" in script

    def test_injection_via_render_engine_raises_value_error(self, mock_gpu):
        malicious = "CYCLES'; import os #"
        with pytest.raises(ValueError, match=r"CYCLES.*import os"):
            render_script.generate_render_config_script(
                job_id=1,
                render_engine=malicious,
                render_device="CPU",
                render_settings=None,
            )

    def test_error_message_includes_rejected_engine_name(self, mock_gpu):
        bad_engine = "NOT_AN_ENGINE"
        with pytest.raises(ValueError) as exc_info:
            render_script.generate_render_config_script(
                job_id=1,
                render_engine=bad_engine,
                render_device="CPU",
                render_settings=None,
            )
        assert bad_engine in str(exc_info.value)
