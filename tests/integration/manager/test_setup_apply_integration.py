# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``run_worker.py --setup-apply`` against a real manager over TLS (setup-rebuild FR-WK-1, FR-WK-3, FR-WK-8).

Real parts: the worker program started as its own process (argument dispatch,
JSON on stdin and stdout, the field checks, the key proof and the signed,
encrypted answer, the certificate check, the one ``config.json`` write, the
worker log) and the manager's ``POST /api/enroll/`` view with the test
database, over TLS on 127.0.0.1 (the ``tls_manager`` fixture). The child runs
with its own scratch home and settings folder (``_worker_cli``); its card
choice is CPU only, so no card is needed, and no Blender is involved.

Field checks, exit codes and error names one by one are unit cases.
"""
import json

from rest_framework.authtoken.models import Token
from workers.models import Worker

from tests.integration._worker_cli import WorkerCli

from ._tls_manager import MANAGER_ID
from .conftest import TEST_ENROLLMENT_KEY_CANONICAL

KEY_AS_TYPED = '4f9x-k2pb-q7m3-n8rt'  # TEST_ENROLLMENT_KEY_CANONICAL as a user may type it
WRONG_KEY = '00000000000000PP'
PAGE_PASSWORD = 'quartz-Lantern-5150'
HASH_PREFIX = 'pbkdf2_sha256$1000000$'


def _request(manager, key):
    host_port = manager.base_url[len('https://'):].split('/')[0]
    return {'compute': 'cpu', 'cards': [], 'combine': False, 'unreal_enabled': False,
            'ui_password': PAGE_PASSWORD, 'manager_url': f'https://{host_port}', 'enrollment_key': key,
            'manager_name': 'Studio A'}


def test_setup_apply_enrolls_and_saves_with_no_secret_on_the_wire_or_in_the_log(tls_manager, tmp_path):
    """FR-WK-1, FR-WK-3, FR-WK-8: a real key enrolls; config.json holds the enrollment and only a hash.

    The manager received a key proof, never the key; the worker log holds
    neither the page password, the key nor the token.
    """
    cli = WorkerCli(tmp_path)

    code, answer, stdout, stderr = cli.run('--setup-apply', _request(tls_manager, KEY_AS_TYPED))

    assert code == 0, (stdout, stderr, cli.log_text())
    # The manager sends no ``status`` yet (worker approval, decision C), so FR-WK-5 passes on null.
    assert answer == {'enrolled': True, 'manager_id': MANAGER_ID, 'manager_name': 'Studio A',
                      'cert_fingerprint': tls_manager.fingerprint, 'status': None}
    saved = json.loads(cli.config_path.read_text(encoding='utf-8'))
    token = saved['manager']['api_token']
    assert Worker.objects.filter(user=Token.objects.get(key=token).user).exists()
    assert (saved['manager']['cert_fingerprint'], saved['manager']['manager_id']) == (tls_manager.fingerprint,
                                                                                      MANAGER_ID)
    assert saved['worker']['ui_password_hash'].startswith(HASH_PREFIX)
    assert PAGE_PASSWORD not in cli.config_path.read_text(encoding='utf-8')
    enroll_posts = [body for path, body in tls_manager.bodies if path == '/api/enroll/']
    assert len(enroll_posts) == 1 and set(json.loads(enroll_posts[0])) == {'hostname', 'nonce', 'proof'}
    wire = enroll_posts[0].upper()
    assert TEST_ENROLLMENT_KEY_CANONICAL.encode() not in wire and KEY_AS_TYPED.upper().encode() not in wire
    log = cli.log_text()
    assert 'enrolled with manager' in log  # the log was written, so its silence below means something
    for secret in (PAGE_PASSWORD, KEY_AS_TYPED, TEST_ENROLLMENT_KEY_CANONICAL, token):
        assert secret.lower() not in log.lower() and secret.lower() not in (stdout + stderr).lower()


def test_setup_apply_with_a_wrong_key_exits_1_invalid_key_and_changes_nothing(tls_manager, tmp_path):
    """FR-WK-1, FR-WK-8: the manager refuses the proof (403); exit 1 ``invalid_key``; config.json is unchanged."""
    cli = WorkerCli(tmp_path)
    cli.worker_dir.mkdir(parents=True)
    cli.config_path.write_text(json.dumps({'compute': {'method': 'gpu', 'revision': 3}}), encoding='utf-8')
    before = cli.config_path.read_bytes()

    code, answer, stdout, stderr = cli.run('--setup-apply', _request(tls_manager, WRONG_KEY))

    assert code == 1, (stdout, stderr, cli.log_text())
    assert answer['error'] == 'invalid_key' and set(answer) == {'error', 'message'}
    assert cli.config_path.read_bytes() == before
    assert not Worker.objects.exists()
