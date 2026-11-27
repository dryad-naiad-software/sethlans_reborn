# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Harness for the setup page browser tests (setup-rebuild chunk 5, FR-WZ-10).

A real ``run_manager --setup`` child (``SetupProcess`` of the setup-mode
integration tests) serves the built page at ``/setup/`` on its Waitress
port; its worker program is ``_fake_worker_cli.py`` (started through
``_setup_mode_fake_worker.py``), which answers from a script and records
every ``--setup-apply`` request. Pages open in Chromium at phone width
(390 x 844).
"""
import contextlib
import json
from pathlib import Path

import pytest

from tests.integration.manager.setup_mode import setup_process
from tests.integration.manager.setup_mode.setup_process import SetupProcess

STARTER = Path(__file__).with_name('_setup_mode_fake_worker.py')
PHONE = {'width': 390, 'height': 844}
FORM_WAIT_MS = 30_000
SUBMIT_WAIT_MS = 60_000
# The form's default port; a setup port equal to it is never probed (no Caddy runs here).
FORM_PORT = 8080

ADMIN_PASSWORD = 'Plain-Admin-Secret-7c4e1f9a'
WORKER_PASSWORD = 'Plain-Worker-Page-5d1a9c3e'
CARD_A = {'address': '0000:01:00.0', 'key': 'pci:0000:01:00.0', 'pci': '01:00.0', 'name': 'GeForce RTX Test A',
          'brand': 'NVIDIA', 'vendor_id': '10de', 'device_id': '2684', 'revision_id': 'a1',
          'screen': False, 'primary_screen': False}
CARD_B = dict(CARD_A, address='0000:02:00.0', key='pci:0000:02:00.0', pci='02:00.0', name='GeForce RTX Test B')
FARM = {'name': 'Render Farm', 'host': 'farm-host', 'ip': '192.0.2.10', 'port': 8443,
        'manager_id': '6f1c2d3e-4b5a-4c6d-8e7f-9a0b1c2d3e4f', 'version': '0.1.0'}
FINGERPRINT = 'AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89'
CARDS_ANSWER = {
    'os_probe_failed': False, 'cards': [CARD_A, CARD_B],
    'saved': {'compute': 'cpu', 'cards': [], 'combine': False, 'env_override': False, 'config_read_failed': False},
    'unreal_enabled': False, 'unreal_enabled_env': False, 'enrollment': None, 'ui_password_set': False,
}
NOT_ENROLLED = {'enrolled': False, 'manager_id': None, 'manager_name': None, 'cert_fingerprint': None, 'status': None}
ENROLLED = {'enrolled': True, 'manager_id': FARM['manager_id'], 'manager_name': FARM['name'],
            'cert_fingerprint': FINGERPRINT, 'status': 'pending'}
GONE_CARD = '0000:09:00.0'
# A saved choice of a card this machine no longer has, beside one it has.
SAVED_GONE_CARD = dict(CARDS_ANSWER, saved=dict(CARDS_ANSWER['saved'], compute='gpu',
                                                cards=[GONE_CARD, CARD_A['address']]))
INVALID_KEY = {'error': 'invalid_key', 'message': 'The manager did not accept the enrollment key.'}


class FakeWorkerCli:
    """The fake worker program's folder: its script and the recorded ``--setup-apply`` requests."""

    def __init__(self, folder: Path, apply_answers, cards_answer=None):
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        script = {'--setup-cards': [0, cards_answer or CARDS_ANSWER], '--setup-discover': [0, {'managers': [FARM]}],
                  '--setup-apply': apply_answers}
        (folder / 'script.json').write_text(json.dumps(script), encoding='utf-8')

    def applied(self) -> list:
        """The ``--setup-apply`` requests, in order."""
        paths = sorted(self.folder.glob('apply-*.json'), key=lambda path: int(path.stem.split('-')[1]))
        return [json.loads(path.read_text(encoding='utf-8')) for path in paths]


@contextlib.contextmanager
def setup_server(folder: Path, apply_answers=((0, NOT_ENROLLED),), cards_answer=None):
    """``(SetupProcess, FakeWorkerCli)``: setup mode with the fake worker program, ended on exit."""
    fake = FakeWorkerCli(folder / 'fake-worker', [list(answer) for answer in apply_answers], cards_answer)
    proc_folder = folder / 'proc'
    proc_folder.mkdir()
    proc = SetupProcess(proc_folder)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(setup_process, 'RUN_MANAGER', STARTER)
        try:
            proc.start(setup_port=FORM_PORT, extra_env={'SETUP_TEST_FAKE_WORKER_DIR': str(fake.folder)})
            yield proc, fake
        finally:
            proc.stop()


@contextlib.contextmanager
def phone_page(browser):
    """A fresh context and page at phone width, closed on exit."""
    context = browser.new_context(viewport=PHONE)
    try:
        yield context.new_page()
    finally:
        context.close()


def setup_url(proc) -> str:
    return f'http://127.0.0.1:{proc.waitress_port}/setup/'


def enter_token(page, token: str) -> None:
    page.get_by_label('Setup token').fill(token)
    page.get_by_role('button', name='Continue').click()


def open_form(page, proc, mode_label=None) -> None:
    """Open the page, enter the token, wait for the form, pick the mode by its label."""
    page.goto(setup_url(proc))
    enter_token(page, proc.token)
    page.get_by_role('heading', name='Mode').wait_for(timeout=FORM_WAIT_MS)
    if mode_label:
        page.get_by_role('radio', name=mode_label).check()


def fill_admin(page) -> None:
    page.get_by_label('Username').fill('farmadmin')
    page.get_by_label('Password', exact=True).fill(ADMIN_PASSWORD)
    page.get_by_label('Password again', exact=True).fill(ADMIN_PASSWORD)


def fill_worker_password(page) -> None:
    page.get_by_label('Worker page password', exact=True).fill(WORKER_PASSWORD)
    page.get_by_label('Worker page password again', exact=True).fill(WORKER_PASSWORD)


def fill_data_folder(page, path: Path) -> None:
    page.get_by_label('Data folder').fill(str(path))


def save(page) -> None:
    page.get_by_role('button', name='Save and start').click()


def logs_and_console(proc) -> str:
    """Everything setup mode logged: its log folder and its console."""
    logs = proc.settings_dir / 'logs'
    texts = [path.read_text(encoding='utf-8', errors='replace') for path in logs.rglob('*') if path.is_file()]
    return '\n'.join(texts + [proc.output()])
