# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup mode with the real worker program (setup-rebuild chunk 5, the API layer).

A real ``run_manager --setup`` child runs the worker program from source
(``run_worker --setup-cards``, ``--setup-discover``, ``--setup-apply``) as its
own child. In the ``worker`` mode the worker enrolls with a live manager over
TLS in this process (``tls_manager``: the real enroll view on the test
database). Every process uses the scratch user folders of the test.

Each ``GET /api/setup/form/`` waits about 5 s for the network discovery.
"""
import configparser
import json
from pathlib import Path

from django.contrib.auth.hashers import check_password
from rest_framework.authtoken.models import Token

from shared.settings_file import remove_key
from workers.models import Worker

from .._tls_manager import MANAGER_ID
from ..conftest import TEST_ENROLLMENT_KEY_CANONICAL
from .setup_process import ADMIN_PASSWORD, EXIT_SAVED, Client, manager_answers

WORKER_PASSWORD = 'Plain-Worker-Page-5d1a9c3e'
WRONG_KEY = '00000000000000PP'   # a valid key shape the manager does not hold: a wrong proof
MANAGER_NAME = 'Farm Manager'
SAVE_TIMEOUT = 15


def _session(proc) -> Client:
    client = Client(proc.waitress_port)
    assert client.auth(proc.token).status == 200
    return client


def _form(client) -> dict:
    resp = client.call('GET', '/api/setup/form/')
    assert resp.status == 200, resp.raw
    form = resp.json()
    assert not [entry for entry in form['errors']
                if entry['part'] == 'worker' and entry['message'].startswith('Could not list')], form['errors']
    return form


def _worker(password: str) -> dict:
    return {'compute': 'cpu', 'cards': [], 'combine': False, 'unreal_enabled': False, 'ui_password': password}


def _worker_answers(data_root: Path, manager: dict, password: str = WORKER_PASSWORD) -> dict:
    return {'mode': 'worker', 'data_folder': {'path': str(data_root)}, 'worker': _worker(password),
            'manager': manager}


def _read_ini(path: Path) -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser(interpolation=None)
    parser.read(path, encoding='utf-8')
    return parser


def _config(proc) -> dict:
    """The worker's ``config.json`` (identity file: always in the settings folder)."""
    return json.loads((proc.settings_dir / 'worker' / 'config.json').read_text(encoding='utf-8'))


def _written(proc, *roots: Path) -> dict:
    """Every file the processes wrote under ``roots``, and the setup child's console."""
    files = {str(path): path.read_bytes() for root in roots for path in root.rglob('*') if path.is_file()}
    files['console'] = proc.output().encode('utf-8')
    return files


def _assert_absent(secret: str, files: dict, skip=()) -> None:
    for name, data in files.items():
        if not name.endswith(skip):
            assert secret.encode('utf-8') not in data, f'secret found in {name}'


class TestSetupWorkerCliIntegration:
    def test_manager_worker_form_and_save(self, setup_process, data_root, tmp_path):
        """FR-WZ-1, FR-WK-1, FR-WK-2, FR-WK-3: the form lists the worker's cards with no password set and no
        enrollment; the save writes sethlans.ini and the worker's own config.json; no secret is logged."""
        proc = setup_process()
        client = _session(proc)

        form = _form(client)
        assert form['mode'] == 'manager_worker'
        assert isinstance(form['cards'], list)
        assert form['worker']['ui_password_set'] is False and form['worker']['ui_password'] == ''
        assert form['enrollment'] is None and form['manager']['keep_enrollment'] is False

        answers = dict(manager_answers(data_root, proc.setup_port), mode='manager_worker',
                       worker=_worker(WORKER_PASSWORD))
        resp = client.submit(answers)

        assert resp.status == 200, resp.raw
        assert resp.json()['worker'] == {'enrolled': False, 'kept_enrollment': False, 'manager_url': None,
                                         'manager_id': None, 'manager_name': None, 'cert_fingerprint': None,
                                         'status': None}
        assert proc.wait(timeout=SAVE_TIMEOUT) == EXIT_SAVED
        ini = _read_ini(proc.ini_path)
        assert ini.get('setup', 'mode') == 'manager_worker'
        assert ini.has_section('first_admin') and ini.has_section('server')
        config = _config(proc)
        assert check_password(WORKER_PASSWORD, config['worker']['ui_password_hash'])
        assert config['compute']['method'] == 'cpu' and config['compute']['cards'] == []
        assert config['unreal']['enabled'] is False
        assert 'manager' not in config or 'api_token' not in config['manager']
        written = _written(proc, tmp_path, data_root)
        for secret in (ADMIN_PASSWORD, WORKER_PASSWORD):
            _assert_absent(secret, written)

    def test_worker_only_enrolls_retries_and_keeps_enrollment(self, tls_manager, setup_process, data_root,
                                                              tmp_path):
        """OQ-7, FR-WK-1, FR-WK-2: a wrong key is refused on its field with nothing saved, and the same
        session may submit again; the right key enrolls and writes only [setup] and [paths].
        FR-RS-3: after a Reset the form shows the enrollment, and keeping it needs no key."""
        url = tls_manager.base_url.removesuffix('/api/')
        proc = setup_process()
        client = _session(proc)

        refused = client.submit(_worker_answers(data_root, {'url': url, 'enrollment_key': WRONG_KEY,
                                                            'name': MANAGER_NAME}))
        assert refused.status == 400, refused.raw
        assert refused.json()['error'] == 'invalid_key'
        assert list(refused.json()['fields']) == ['manager.enrollment_key']
        assert not proc.ini_path.exists()
        assert not Worker.objects.exists()

        saved = client.submit(_worker_answers(data_root, {'url': url, 'enrollment_key': TEST_ENROLLMENT_KEY_CANONICAL,
                                                          'name': MANAGER_NAME}))
        assert saved.status == 200, saved.raw
        finish = saved.json()
        assert finish['restarting'] is False and finish['url'] is None
        assert finish['worker'] == {'enrolled': True, 'kept_enrollment': False, 'manager_url': url,
                                    'manager_id': MANAGER_ID, 'manager_name': MANAGER_NAME,
                                    'cert_fingerprint': tls_manager.fingerprint, 'status': None}
        assert proc.wait(timeout=SAVE_TIMEOUT) == EXIT_SAVED
        assert set(_read_ini(proc.ini_path).sections()) == {'setup', 'paths'}
        worker = Worker.objects.get()
        api_token = Token.objects.get(user=worker.user).key
        assert _config(proc)['manager']['api_token'] == api_token
        written = _written(proc, tmp_path, data_root)
        for secret in (TEST_ENROLLMENT_KEY_CANONICAL, WRONG_KEY, WORKER_PASSWORD):
            _assert_absent(secret, written)
        _assert_absent(api_token, written, skip=('config.json',))
        assert api_token.encode() not in saved.raw

        # Reset setup: the launcher removes [setup] completed_at only, then starts setup mode again.
        proc.stop()
        remove_key('setup', 'completed_at', path=proc.ini_path)
        proc = setup_process()
        client = _session(proc)
        form = _form(client)
        assert form['mode'] == 'worker'
        assert form['enrollment']['manager_name'] == MANAGER_NAME
        assert form['enrollment']['manager_id'] == MANAGER_ID
        assert form['manager']['keep_enrollment'] is True and form['worker']['ui_password_set'] is True

        kept = client.submit(_worker_answers(data_root, {'keep_enrollment': True}, password=''))

        assert kept.status == 200, kept.raw
        assert kept.json()['worker']['kept_enrollment'] is True
        assert kept.json()['worker']['manager_name'] == MANAGER_NAME
        assert proc.wait(timeout=SAVE_TIMEOUT) == EXIT_SAVED
        assert _config(proc)['manager']['api_token'] == api_token
        assert Worker.objects.count() == 1
