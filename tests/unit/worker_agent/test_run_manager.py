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
# tests/unit/test_run_manager.py
"""
Unit tests for the user-facing manager runner script.
"""
import pytest
from unittest.mock import MagicMock

# Module to be tested
import run_manager


@pytest.fixture
def mock_runner_dependencies(mocker):
    """Mocks dependencies for testing the run_manager script's helpers."""
    mock_getenv = mocker.patch('os.getenv')
    mock_config_parser = MagicMock()
    mocker.patch('configparser.ConfigParser', return_value=mock_config_parser)
    # Mock the file system check
    mocker.patch('pathlib.Path.exists', return_value=True)

    return {
        "getenv": mock_getenv,
        "parser": mock_config_parser
    }


def test_get_manager_port_uses_default(mock_runner_dependencies):
    """
    Tests that the default port is returned when no env var or ini setting exists.
    """
    # Arrange
    mock_runner_dependencies["getenv"].return_value = None
    mock_runner_dependencies["parser"].has_option.return_value = False

    # Act
    port = run_manager.get_manager_port()

    # Assert
    assert port == '7075'


def test_get_manager_port_uses_ini_file(mock_runner_dependencies):
    """
    Tests that the port from manager.ini is used when no env var is set.
    """
    # Arrange
    mock_runner_dependencies["getenv"].return_value = None
    mock_runner_dependencies["parser"].has_option.return_value = True
    mock_runner_dependencies["parser"].get.return_value = '8080'

    # Act
    port = run_manager.get_manager_port()

    # Assert
    assert port == '8080'
    mock_runner_dependencies["parser"].get.assert_called_once_with('server', 'port')


def test_get_manager_port_env_var_overrides_ini(mock_runner_dependencies):
    """
    Tests that the SETHLANS_MANAGER_PORT environment variable overrides the .ini file.
    """
    # Arrange
    mock_runner_dependencies["getenv"].return_value = '9999' # Env var is set
    mock_runner_dependencies["parser"].has_option.return_value = True
    mock_runner_dependencies["parser"].get.return_value = '8080' # Ini is also set

    # Act
    port = run_manager.get_manager_port()

    # Assert
    assert port == '9999'
    mock_runner_dependencies["getenv"].assert_called_once_with('SETHLANS_MANAGER_PORT')
    mock_runner_dependencies["parser"].get.assert_not_called()
