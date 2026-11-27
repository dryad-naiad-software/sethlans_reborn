# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The worker page password hash and its rules (setup-rebuild FR-WK-3, FR-WK-4, FR-WZ-6).

Django is in the test environment, so the format is checked against
Django's own hashers in both directions. A hash made by ``make`` costs one
real 1,000,000-round PBKDF2; the Django-made hash uses few rounds.
"""
import re

import pytest
from django.contrib.auth.hashers import PBKDF2PasswordHasher, check_password, identify_hasher
from django.contrib.auth.password_validation import CommonPasswordValidator

from sethlans_worker_agent.web_ui import password_hash, password_rules
from tests.unit.worker._page_password import fast_hash

PASSWORD = 'correct-horse-battery'


class TestHash:

    def test_made_hash_is_read_by_djangos_check_password(self):
        """FR-WK-3: Django's text format at 1,000,000 rounds with a 16-byte salt; Django wants no update."""
        encoded = password_hash.make(PASSWORD)
        assert re.fullmatch(r'pbkdf2_sha256\$1000000\$[0-9a-f]{32}\$[A-Za-z0-9+/]{43}=', encoded)
        assert check_password(PASSWORD, encoded) is True
        assert identify_hasher(encoded).must_update(encoded) is False

    def test_djangos_own_hash_verifies(self):
        """FR-WK-3: a hash Django made (any round count) is checked by the worker without Django."""
        encoded = PBKDF2PasswordHasher().encode(PASSWORD, 'DjangoSalt12345678901234', iterations=1000)
        assert password_hash.verify(PASSWORD, encoded) is True
        assert password_hash.verify(PASSWORD + 'x', encoded) is False

    @pytest.mark.parametrize('encoded', [
        None, '', 'argon2$1000$salt$' + 'A' * 43 + '=', fast_hash(PASSWORD).replace('$1000$', '$0$'),
        fast_hash(PASSWORD).replace('$1000$', '$10000001$'), fast_hash(PASSWORD)[:-2],
        fast_hash(PASSWORD) + '$extra', 'pbkdf2_sha256$1000$sält$' + 'A' * 43 + '=',
    ], ids=['none', 'empty', 'other-algorithm', 'zero-rounds', 'over-max-rounds', 'short-digest', 'five-parts',
            'non-ascii'])
    def test_unusable_value_never_verifies(self, encoded):
        """FR-WK-3: a value this module cannot check (or one that would tie up a thread) is refused, never raised on."""
        assert password_hash.parse(encoded) is None
        assert password_hash.verify(PASSWORD, encoded) is False


class TestRules:

    @pytest.mark.parametrize('password, hostname, found', [
        ('abcdefgx', 'render-node', []),
        ('abcdefx', 'render-node', [password_rules.TOO_SHORT]),
        ('73914682', 'render-node', [password_rules.ENTIRELY_NUMERIC]),
        ('render-node', 'render-node', [password_rules.TOO_SIMILAR]),
        ('workstation1', 'studio-workstation', [password_rules.TOO_SIMILAR]),
        ('render-node', None, []),
        ('pässwörd ok', 'render-node', []),
        ('password€1', 'render-node', [password_rules.NOT_SENDABLE]),
        ('pass\tword1', 'render-node', [password_rules.NOT_SENDABLE]),
        (' sunflower7', 'render-node', [password_rules.EDGE_SPACE]),
        ('sunflower7 ', 'render-node', [password_rules.EDGE_SPACE]),
        (12345678, 'render-node', ['The password must be text.']),
    ], ids=['8-chars-ok', '7-chars', 'all-digits', 'same-as-hostname', 'like-a-hostname-part', 'no-hostname',
            'latin-1-and-inner-space', 'euro-sign', 'tab', 'leading-space', 'trailing-space', 'not-text'])
    def test_rules(self, password, hostname, found):
        """FR-WK-3, FR-WK-4: at least 8 characters, not all digits, not like the computer name, Latin-1, no edge space."""
        assert password_rules.problems(password, hostname) == found

    @pytest.mark.parametrize('password, found', [
        ('PassWord1', [password_rules.TOO_COMMON]),
        (' password1 ', [password_rules.TOO_COMMON, password_rules.EDGE_SPACE]),
        ('sunflower-7e8f', []),
    ], ids=['any-case', 'stripped-like-django', 'not-common'])
    def test_common_password(self, password, found):
        """FR-WK-3: Django's CommonPasswordValidator rule, compared lowercased and stripped as Django does."""
        assert password_rules.problems(password, 'render-node') == found

    def test_list_is_djangos_list_and_message(self):
        """FR-WK-3: the shipped list is Django's own (the worker has no Django), with Django's message."""
        django_rule = CommonPasswordValidator()
        assert password_rules._common_passwords() == django_rule.passwords
        assert password_rules.TOO_COMMON == str(django_rule.get_error_message())

    def test_missing_list_raises_never_passes(self, monkeypatch, tmp_path):
        """FR-WK-3: a broken install without the list fails loudly instead of letting every password through."""
        monkeypatch.setattr(password_rules, 'COMMON_PASSWORDS_PATH', tmp_path / 'absent.txt.gz')
        monkeypatch.setattr(password_rules, '_common', None)
        with pytest.raises(FileNotFoundError):
            password_rules.problems('sunflower-7e8f', 'render-node')
