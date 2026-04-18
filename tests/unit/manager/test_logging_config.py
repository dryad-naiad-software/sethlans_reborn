# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``sethlans_manager.logging_config``.

Verifies the shape of the ``LOGGING`` dict, that ``configure()`` applies
it via ``logging.config.dictConfig``, that repeat calls are idempotent,
and that ``LOGS_DIR`` is created at import time.
"""

import importlib
import logging
from unittest.mock import patch

import pytest

from sethlans_manager import logging_config


def test_logging_dict_shape():
    """The exported ``LOGGING`` dict has the expected top-level structure.

    Covers the regression we were trying to fix: ``mail_admins`` must NOT
    appear as a handler, because we control the dict now and Django's
    ``DEFAULT_LOGGING`` bootstrap is disabled.
    """
    cfg = logging_config.LOGGING

    assert cfg['version'] == 1
    assert 'formatters' in cfg
    assert 'handlers' in cfg
    assert 'loggers' in cfg

    # The whole reason we're here — no mail_admins handler.
    assert 'mail_admins' not in cfg['handlers']

    # Our expected handlers are present.
    assert 'console' in cfg['handlers']
    assert 'file' in cfg['handlers']


def test_configure_applies_dictconfig():
    """``configure()`` calls ``dictConfig`` once with the ``LOGGING`` dict."""
    with patch.object(
        logging_config.logging.config, 'dictConfig',
    ) as mock_dict_config:
        logging_config.configure()

    mock_dict_config.assert_called_once_with(logging_config.LOGGING)


def test_configure_idempotent():
    """Calling ``configure()`` twice does not raise, and root still works.

    ``dictConfig`` replaces the existing handlers on each call with a
    fresh set built from the dict — no accumulation, no errors.
    """
    logging_config.configure()
    logging_config.configure()  # Must not raise.

    root = logging.getLogger()
    # Root logger ('') has both console and file handlers per the config.
    handler_classes = {type(h).__name__ for h in root.handlers}
    assert 'StreamHandler' in handler_classes
    assert 'RotatingFileHandler' in handler_classes


def test_logs_dir_created(tmp_path, monkeypatch):
    """Reloading the module with a patched data dir creates ``LOGS_DIR``."""
    fake_data_root = tmp_path / "manager-data"

    # Patch both the is_frozen signal and the data-dir resolver so the
    # module thinks it's running in frozen mode and should write logs
    # under our tmp_path.
    import shared.frozen_paths as frozen_paths
    monkeypatch.setattr(frozen_paths, 'is_frozen', lambda: True)
    monkeypatch.setattr(
        frozen_paths, 'get_data_dir', lambda app: fake_data_root,
    )

    reloaded = importlib.reload(logging_config)
    try:
        expected = fake_data_root / 'logs'
        assert reloaded.LOGS_DIR == expected
        assert expected.is_dir()
    finally:
        # Restore real module state so later tests see the correct
        # LOGS_DIR / LOGGING dict.
        importlib.reload(logging_config)


@pytest.fixture(autouse=True)
def _restore_root_handlers():
    """Snapshot and restore root-logger handlers around each test.

    ``configure()`` mutates the root logger globally; without this the
    idempotent-call test would leak handlers into sibling tests.
    """
    root = logging.getLogger()
    prev = list(root.handlers)
    yield
    root.handlers = prev
