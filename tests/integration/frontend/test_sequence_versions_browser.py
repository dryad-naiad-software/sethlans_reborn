# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Sequence versions on the Unreal project page, in a real browser (Unreal
sequence rendering chunk 8: FR-SU-3, FR-SU-4, FR-SL-4, FR-SS-6).

Chromium drives the built Angular app served by the real manager
(``_manager_worker_list_browser``) and logs in as a staff user.  One
version is written straight to the database (``_sequence_browser``), with
shot 1 DONE, shot 2 ERROR and its audio job still queued; nothing renders.
The test reads the versions list and the shots, checks that only the DONE
shot offers downloads, cancels the version through the page and checks
that the API ran and the page shows the new status.  Shot 2's name is
markup and must appear as text.

These tests use the database, so they do not use pytest-playwright's
``page`` (issue #277); see ``lb.own_chromium``.
"""

import pytest
from playwright.sync_api import expect

from workers.models import Job, SequenceSubmission

from . import _manager_worker_list_browser as lb
from . import _sequence_browser as seed

pytestmark = lb.needs_built_frontend

ASYNC_UNSAFE = 'DJANGO_ALLOW_ASYNC_UNSAFE'


@pytest.fixture(scope='module')
def chromium():
    """This module's own Chromium, stopped when the module ends (issue #277)."""
    with lb.own_chromium() as browser:
        yield browser


@pytest.fixture
def ui_page(chromium):
    context = chromium.new_context()
    yield context.new_page()
    context.close()


@pytest.fixture
def _orm_beside_playwright(monkeypatch):
    """Allow ORM calls in the main thread while this module's Playwright runs; before ``transactional_db``."""
    monkeypatch.setenv(ASYNC_UNSAFE, 'true')


@pytest.fixture
def manager(_orm_beside_playwright, transactional_db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    with lb.served_manager(settings, tmp_path) as base_url:
        yield base_url


def _shot_row(page, index):
    return page.locator('app-sequence-version-shots tr.shot').nth(index)


def test_mixed_version_shows_shots_downloads_and_cancels(manager, ui_page):
    seeded = seed.seed_mixed_version()
    submission = seeded.submission
    errors = lb.watch_page(ui_page)
    lb.log_in(ui_page, manager)
    ui_page.goto(f'{manager}/projects/{seeded.project_id}')

    version = ui_page.locator('app-sequence-versions .version')
    expect(version).to_have_count(1)
    expect(version.locator('.version-name')).to_have_text(seed.VERSION_NAME)
    expect(version.locator('.version-status')).to_have_text('Rendering')
    expect(version.locator('.version-shots')).to_have_text('Shots 1/2 (1 failed)')
    # Version level: the frames of the DONE shot exist; no video or sound was asked for or built.
    expect(version.locator('.line a.download').first).to_have_attribute('data-kind', 'frames')

    version.locator('button.toggle-shots').click()
    expect(ui_page.locator('app-sequence-version-shots tr.shot')).to_have_count(2)
    done, failed = _shot_row(ui_page, 0), _shot_row(ui_page, 1)
    expect(done.locator('.shot-name')).to_have_text(seed.DONE_SHOT)
    expect(done.locator('.shot-status')).to_have_text('DONE')
    expect(done.locator('.shot-error')).to_have_count(0)
    expect(failed.locator('.shot-status')).to_have_text('ERROR')
    expect(failed.locator('.shot-error')).to_have_text(seed.SHOT_ERROR)
    expect(failed.locator('button.rerender')).to_be_visible()
    expect(done.locator('button.rerender')).to_have_count(0)

    # Downloads only for the DONE shot: its frames, as a plain link to the shot's route.
    expect(done.locator('a.download')).to_have_count(1)
    expect(done.locator('a.download')).to_have_attribute(
        'href', f'/api/sequence-submissions/{submission.pk}/shots/1/download-frames/')
    expect(failed.locator('a.download')).to_have_count(0)

    # The hostile shot name is text, not markup.
    expect(failed.locator('.shot-name')).to_have_text(seed.HOSTILE_SHOT)
    assert failed.locator('.shot-name b').count() == 0
    assert ui_page.locator('app-sequence-versions b').count() == 0

    version.locator('button.cancel').click()
    dialog = ui_page.locator('mat-dialog-container')
    expect(dialog).to_contain_text(f'Cancel {seed.VERSION_NAME}?')
    with ui_page.expect_response(lambda r: r.url.endswith(f'/{submission.pk}/cancel/')) as posted:
        dialog.locator('button', has_text='Cancel version').click()
    assert posted.value.request.method == 'POST'
    assert posted.value.status == 200
    assert posted.value.json() == {'status': 'CANCELED', 'canceled': 1}

    expect(version.locator('.version-status')).to_have_text('Canceled')
    expect(version.locator('button.cancel')).to_have_count(0)
    expect(version.locator('.version-shots')).to_have_text('Shots 1/2 (1 failed)')
    row = SequenceSubmission.objects.get(pk=submission.pk)
    assert (row.status, row.cancel_requested) == ('CANCELED', True)
    assert Job.objects.get(pk=seeded.audio.pk).status == 'CANCELED'
    assert errors == []
