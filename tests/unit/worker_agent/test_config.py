#
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
#
# Created by Mario Estrella on 7/31/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# tests/unit/worker_agent/test_config.py
"""
Unit tests for the worker agent's configuration loading module.
"""

import pytest
from unittest.mock import MagicMock

# Module to be tested
from sethlans_worker_agent import config


@pytest.fixture
def mock_config_dependencies(mocker):
    """Mocks os.getenv and configparser for testing the config helper."""
    mock_getenv = mocker.patch('os.getenv')
    mock_config_parser = MagicMock()
    mocker.patch('configparser.ConfigParser', return_value=mock_config_parser)
    # Re-initialize the module-level parser to use our mock
    config.config_parser = mock_config_parser

    return {
        "getenv": mock_getenv,
        "parser": mock_config_parser
    }


def test_get_config_value_uses_default(mock_config_dependencies):
    """
    Tests that the default value is returned when no env var or ini setting exists.
    """
    # Arrange
    mock_config_dependencies["getenv"].return_value = None
    mock_config_dependencies["parser"].has_option.return_value = False

    # Act
    result = config.get_config_value('manager', 'port', '7075', is_int=True)

    # Assert
    assert result == 7075


def test_get_config_value_uses_ini_file(mock_config_dependencies):
    """
    Tests that the value from the .ini file is used when no env var is set.
    """
    # Arrange
    mock_config_dependencies["getenv"].return_value = None
    mock_config_dependencies["parser"].has_option.return_value = True
    mock_config_dependencies["parser"].getint.return_value = 8080

    # Act
    result = config.get_config_value('manager', 'port', '7075', is_int=True)

    # Assert
    assert result == 8080
    mock_config_dependencies["parser"].getint.assert_called_once_with('manager', 'port')


def test_get_config_value_env_var_overrides_ini(mock_config_dependencies):
    """
    Tests that the environment variable takes precedence over the .ini file.
    """
    # Arrange
    mock_config_dependencies["getenv"].return_value = '9000' # Env var is set
    mock_config_dependencies["parser"].has_option.return_value = True
    mock_config_dependencies["parser"].getint.return_value = 8080 # Ini is also set

    # Act
    result = config.get_config_value('manager', 'port', '7075', is_int=True)

    # Assert
    assert result == 9000
    mock_config_dependencies["getenv"].assert_called_once_with('SETHLANS_MANAGER_PORT')
    mock_config_dependencies["parser"].getint.assert_not_called()


def test_get_config_value_handles_string_value(mock_config_dependencies):
    """
    Tests that string values are returned correctly when is_int=False.
    """
    # Arrange
    mock_config_dependencies["getenv"].return_value = None
    mock_config_dependencies["parser"].has_option.return_value = True
    mock_config_dependencies["parser"].get.return_value = "testhost"

    # Act
    result = config.get_config_value('manager', 'host', '127.0.0.1', is_int=False)

    # Assert
    assert result == "testhost"