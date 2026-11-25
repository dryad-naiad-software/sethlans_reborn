# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``entry.hash_password`` (``run_manager --hash-password``): stdin cap, validators, output."""
import io

import pytest
from django.contrib.auth.hashers import check_password

from sethlans_manager.setup_mode import entry
from tests.unit.manager.setup_mode._helpers import FAKE_HASH, GOOD_PASSWORD


class _Bytes(io.StringIO):
    """A text stdin whose ``buffer`` holds raw bytes, like ``sys.stdin``."""

    def __init__(self, data: bytes):
        super().__init__()
        self.buffer = io.BytesIO(data)


def _hash(data, mocker=None):
    if mocker is not None:
        mocker.patch("django.contrib.auth.hashers.make_password", return_value=FAKE_HASH)
    stdout, stderr = io.StringIO(), io.StringIO()
    code = entry.hash_password(_Bytes(data) if isinstance(data, bytes) else io.StringIO(data), stdout, stderr)
    return code, stdout.getvalue(), stderr.getvalue()


class TestHashPasswordOutput:

    def test_prints_one_real_pbkdf2_hash_line(self):
        code, out, err = _hash((GOOD_PASSWORD + "\n").encode())
        assert code == entry.EXIT_OK
        assert err == ""
        assert out.endswith("\n") and out.count("\n") == 1
        hashed = out.strip()
        assert hashed.startswith("pbkdf2_sha256$1000000$")
        assert check_password(GOOD_PASSWORD, hashed)

    @pytest.mark.parametrize("data, expected", [
        (b"Pw-For-Tests-42\r\n", "Pw-For-Tests-42"),
        (b"Pw-For-Tests-42\n", "Pw-For-Tests-42"),
        (b"Pw-For-Tests-42", "Pw-For-Tests-42"),
        (b"Pw-For-Tests-42\n\n", "Pw-For-Tests-42\n"),
        (b" Pw-For-Tests-42 \n", " Pw-For-Tests-42 "),
    ])
    def test_strips_exactly_one_line_ending(self, mocker, data, expected):
        maker = mocker.patch("django.contrib.auth.hashers.make_password", return_value=FAKE_HASH)
        code, out, _err = _hash(data)
        assert code == entry.EXIT_OK
        maker.assert_called_once_with(expected)
        assert out == FAKE_HASH + "\n"

    def test_text_stdin_without_buffer_works(self, mocker):
        maker = mocker.patch("django.contrib.auth.hashers.make_password", return_value=FAKE_HASH)
        code, _out, _err = _hash("Pw-For-Tests-42\n")
        assert code == entry.EXIT_OK
        maker.assert_called_once_with("Pw-For-Tests-42")


class TestHashPasswordRefusals:

    @pytest.mark.parametrize("data", [b"", b"\n", b"\r\n"])
    def test_empty_is_exit_1(self, mocker, data):
        code, out, err = _hash(data, mocker)
        assert (code, out) == (entry.EXIT_INVALID, "")
        assert err.strip() == "No password on stdin."

    @pytest.mark.parametrize("ending", [b"", b"\n", b"\r\n", b"\r"])
    def test_4096_bytes_pass_with_or_without_a_line_ending(self, mocker, ending):
        maker = mocker.patch("django.contrib.auth.hashers.make_password", return_value=FAKE_HASH)
        code, out, _err = _hash(b"Zq7-" * 1024 + ending)
        assert code == entry.EXIT_OK
        assert out == FAKE_HASH + "\n"
        maker.assert_called_once_with("Zq7-" * 1024)  # the ending is stripped before the cap

    @pytest.mark.parametrize("ending", [b"", b"\n", b"\r\n", b"\r"])
    def test_4097_bytes_are_refused_with_or_without_a_line_ending(self, mocker, ending):
        code, out, err = _hash(b"Zq7-" * 1024 + b"x" + ending, mocker)
        assert (code, out) == (entry.EXIT_INVALID, "")
        assert err.strip() == "The password is longer than 4096 bytes."

    def test_4096_bytes_and_two_line_endings_are_refused(self, mocker):
        # Only one ending is stripped; the second counts as password bytes.
        code, _out, err = _hash(b"Zq7-" * 1024 + b"\n\n", mocker)
        assert code == entry.EXIT_INVALID
        assert err.strip() == "The password is longer than 4096 bytes."

    def test_a_much_longer_stdin_is_refused(self, mocker):
        code, _out, err = _hash(b"a" * 100_000 + b"\r\n", mocker)
        assert code == entry.EXIT_INVALID
        assert err.strip() == "The password is longer than 4096 bytes."

    def test_text_stdin_at_the_cap(self, mocker):
        maker = mocker.patch("django.contrib.auth.hashers.make_password", return_value=FAKE_HASH)
        code, _out, _err = _hash("Zq7-" * 1024 + "\r\n")
        assert code == entry.EXIT_OK
        maker.assert_called_once_with("Zq7-" * 1024)

    def test_multibyte_characters_count_as_bytes(self, mocker):
        # 2049 two-byte characters are 4098 bytes.
        code, _out, err = _hash(("é" * 2049).encode("utf-8") + b"\n", mocker)
        assert code == entry.EXIT_INVALID
        assert err.strip() == "The password is longer than 4096 bytes."

    def test_not_utf8_is_exit_1(self, mocker):
        code, out, err = _hash(b"\xff\xfeabc\n", mocker)
        assert (code, out) == (entry.EXIT_INVALID, "")
        assert err.strip() == "The password is not UTF-8."

    @pytest.mark.parametrize("password, message", [
        ("password", "This password is too common."),
        ("Ab1-x", "This password is too short. It must contain at least 8 characters."),
        ("8417305926", "This password is entirely numeric."),
    ])
    def test_validators_refuse_with_their_messages(self, mocker, password, message):
        code, out, err = _hash((password + "\n").encode(), mocker)
        assert (code, out) == (entry.EXIT_INVALID, "")
        assert message in err.splitlines()
        assert password not in err.replace(message, "")


class TestSensitiveVariables:

    @pytest.mark.parametrize("function, names", [
        (entry.hash_password, ("password",)),
        (entry._read_password, ("raw", "password")),
    ])
    def test_marked_for_django_error_reports(self, function, names):
        import inspect

        assert function.__code__.co_name == "sensitive_variables_wrapper"
        assert inspect.getclosurevars(function).nonlocals["variables"] == names
