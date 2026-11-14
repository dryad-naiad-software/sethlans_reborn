# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A failing post-commit callback does not drop the ones after it (issue #225).

Django runs ``transaction.on_commit`` callbacks in order after the commit.
A non-robust callback that raises stops the loop: every later callback is
dropped and the exception escapes into a caller whose write has already
committed.  The video-assembly thread start and the two thumbnail
callbacks, the project manifest update and the video retry thread start
are now registered with ``robust=True`` (as tile assembly already was,
#219), so Django logs the error and runs the rest.

The tests need a real commit (``transaction=True``):
``captureOnCommitCallbacks`` ignores ``robust``.
"""

from unittest.mock import MagicMock

import pytest
from django.db import transaction

from workers import parent_dispatch, signals, thumbnail_signals
from workers.views import animation_video_actions
from workers.models import Animation, AnimationFrame, Job

VIDEO_SETTINGS = {
    'preset': 'web_h264', 'framerate': 24, 'container': 'mp4',
    'codec': 'libx264', 'crf': 23,
}


def _raise(*_args, **_kwargs):
    raise RuntimeError('boom')


def _commit_with_later_callback(register):
    """Run ``register()`` in a transaction, then queue a marker callback.

    Returns whether the marker ran after the commit.
    """
    ran = []
    with transaction.atomic():
        register()
        transaction.on_commit(lambda: ran.append(True))
    return ran == [True]


def _animation(project, asset, name, **extra):
    return Animation.objects.create(
        project=project, asset=asset, name=name,
        output_file_pattern='//render/f_####.png',
        start_frame=1, end_frame=1, **extra,
    )


@pytest.mark.django_db(transaction=True)
def test_failing_video_thread_start_keeps_later_callbacks(mocker, project, asset):
    thread = MagicMock()
    thread.return_value.start.side_effect = RuntimeError('cannot start thread')
    mocker.patch.object(parent_dispatch.threading, 'Thread', thread)
    anim = _animation(project, asset, 'RobustVid1',
                      video_settings=VIDEO_SETTINGS, video_status='PENDING')

    assert _commit_with_later_callback(
        lambda: parent_dispatch._trigger_video_assembly(anim),
    )
    thread.return_value.start.assert_called_once()
    anim.refresh_from_db()
    assert anim.video_status == 'ASSEMBLING'


@pytest.mark.django_db(transaction=True)
def test_failing_job_thumbnail_keeps_later_callbacks(asset):
    job = Job.objects.create(
        name='RobustThumb1', asset=asset, output_file_pattern='//render/#.png',
        output_file='outputs/robust_thumb.png',
    )
    generate = MagicMock(side_effect=RuntimeError('bad image'))

    assert _commit_with_later_callback(
        lambda: thumbnail_signals.schedule_job_thumbnail(job, generate, handler=None),
    )
    generate.assert_called_once()


@pytest.mark.django_db(transaction=True)
def test_failing_frame_thumbnail_keeps_later_callbacks(project, asset):
    anim = _animation(project, asset, 'RobustFrm1')
    frame = AnimationFrame.objects.create(
        animation=anim, frame_number=1, output_file='frames/robust_frame.png',
    )
    generate = MagicMock(side_effect=RuntimeError('bad image'))

    assert _commit_with_later_callback(
        lambda: thumbnail_signals.schedule_animation_frame_thumbnail(
            frame, generate, handler=None,
        ),
    )
    generate.assert_called_once()


@pytest.mark.django_db(transaction=True)
def test_failing_manifest_update_keeps_later_callbacks(mocker, project, asset):
    update = mocker.patch.object(
        signals, 'update_project_manifest', side_effect=RuntimeError('disk full'),
    )

    assert _commit_with_later_callback(lambda: Job.objects.create(
        name='RobustManif', asset=asset, output_file_pattern='//render/#.png',
    ))
    update.assert_called_once_with(project.pk)


@pytest.mark.django_db(transaction=True)
def test_failing_video_retry_thread_start_keeps_later_callbacks(
    mocker, admin_client, project, asset,
):
    thread = MagicMock()
    thread.return_value.start.side_effect = RuntimeError('cannot start thread')
    mocker.patch.object(animation_video_actions.threading, 'Thread', thread)
    anim = _animation(project, asset, 'RobustRetry1',
                      video_settings=VIDEO_SETTINGS, video_status='ERROR')
    responses = []

    assert _commit_with_later_callback(lambda: responses.append(
        admin_client.post(f'/api/animations/{anim.pk}/retry-video/'),
    ))
    assert responses[0].status_code == 200, responses[0].data
    thread.return_value.start.assert_called_once()
    anim.refresh_from_db()
    assert anim.video_status == 'ASSEMBLING'
