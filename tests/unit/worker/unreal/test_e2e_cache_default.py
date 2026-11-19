# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The real Unreal E2E's default cache dir passes the worker's own cache-dir
check, so an opted-in run without ``SETHLANS_UNREAL_E2E_CACHE_DIR`` does
not turn Unreal off (``tests/e2e/unreal_e2e_config.py``).
"""
import sys

import pytest

from sethlans_worker_agent.unreal_executor import cache_dir
from tests.e2e import unreal_e2e_config

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='CACHE_DIR rules are Windows paths (phase 1)')


def test_default_has_no_short_name():
    assert '~' not in unreal_e2e_config.DEFAULT_CACHE_DIR


@pytest.mark.skipif(' ' in unreal_e2e_config.DEFAULT_CACHE_DIR,
                    reason='temp dir has a space: the E2E needs SETHLANS_UNREAL_E2E_CACHE_DIR here')
def test_default_passes_the_worker_cache_dir_check():
    cache_dir.check_cache_dir_text(unreal_e2e_config.DEFAULT_CACHE_DIR, env={})
