# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``auth.load_token``: the ``<settings>/setup/setup_token`` checks."""
import pytest

from sethlans_manager.setup_mode import auth
from tests.unit.manager.setup_mode._helpers import TOKEN


@pytest.fixture
def token_file(settings_dir):
    folder = settings_dir / "setup"
    folder.mkdir(parents=True)
    return folder / "setup_token"


def _error(path=None) -> str:
    with pytest.raises(auth.SetupTokenError) as info:
        auth.load_token(path)
    return str(info.value)


class TestTokenPath:

    def test_default_path_is_in_the_settings_folder(self, settings_dir):
        assert auth.token_path() == settings_dir / "setup" / "setup_token"


class TestLoadToken:

    def test_reads_and_strips_the_token(self, token_file):
        token_file.write_bytes(f"  {TOKEN}\r\n".encode())
        assert auth.load_token() == TOKEN

    def test_512_bytes_are_accepted(self, token_file):
        token_file.write_bytes(b"a" * 512)
        assert auth.load_token(token_file) == "a" * 512

    def test_missing_file(self, token_file):
        assert _error(token_file) == f"No setup token at {token_file}; start setup from the launcher"

    @pytest.mark.parametrize("data", [b"", b"  \n\t "])
    def test_empty_file(self, token_file, data):
        token_file.write_bytes(data)
        assert _error(token_file) == f"The setup token at {token_file} is empty"

    def test_513_bytes_are_too_long(self, token_file):
        token_file.write_bytes(b"a" * 513)
        assert _error(token_file) == f"The setup token at {token_file} is too long"

    def test_non_ascii(self, token_file):
        token_file.write_bytes("tokén".encode("utf-8"))
        assert _error(token_file) == f"The setup token at {token_file} is not ASCII"

    def test_not_owned_by_this_user(self, token_file, mocker):
        token_file.write_bytes(TOKEN.encode())
        owned = mocker.patch.object(auth.owner_only_acl, "is_owned_by_current_user", return_value=False)
        message = _error(token_file)
        assert message == f"The setup token at {token_file} is not owned by the current user"
        owned.assert_called_once_with(token_file)

    def test_a_folder_in_its_place_cannot_be_read(self, token_file):
        token_file.mkdir()
        assert _error(token_file).startswith(f"Cannot read the setup token at {token_file}: ")

    @pytest.mark.parametrize("data", [b"a" * 513, "tokén-secret".encode()])
    def test_message_never_quotes_the_content(self, token_file, data):
        token_file.write_bytes(data)
        message = _error(token_file)
        assert "aaaa" not in message
        assert "secret" not in message


class TestConfigure:

    def test_current_without_configure_raises(self):
        with pytest.raises(RuntimeError, match="no setup token configured"):
            auth.current()

    def test_empty_token_is_refused(self):
        with pytest.raises(ValueError):
            auth.SetupAuth("")

    def test_configure_installs_the_process_auth(self):
        installed = auth.configure(TOKEN)
        assert auth.current() is installed
