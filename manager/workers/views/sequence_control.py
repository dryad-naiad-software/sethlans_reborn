# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Control actions of a sequence version (Unreal sequence rendering FR-SS-5,
FR-SS-6, FR-SS-7, FR-SV-6, FR-SV-8, FR-SL-5): admin only.

``cancel/``, ``shots/<index>/rerender/``, ``DELETE``, ``retry-sound/``,
``retry-video/`` and ``merge-sound/``.  Each runs one service call in its
own transaction (``services/sequence_control.py``,
``services/sequence_delete.py``, ``services/sequence_sound.py``,
``services/sequence_assembly.py``); a refusal is ``{"error": text}`` with
its status.
"""

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models import SequenceSubmission
from ..services.sequence_assembly import merge_sound, retry_video
from ..services.sequence_control import cancel_submission, rerender_shot
from ..services.sequence_delete import delete_submission
from ..services.sequence_sound import RetryRefused, retry_sound

_TAGS = ['Management UI']
_ERROR = inline_serializer('SequenceSubmissionActionError', {'error': serializers.CharField()})
_STATE = {'status': serializers.CharField()}


def _responses(name, fields, *codes):
    return {200: inline_serializer(name, fields), **{code: _ERROR for code in codes}}


class SequenceControlMixin:
    """The FR-SS and FR-SV actions of ``SequenceSubmissionViewSet``."""

    def _run(self, function, ok, *args):
        submission = self.get_object()
        try:
            value = function(submission.pk, *args)
        except SequenceSubmission.DoesNotExist:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        except RetryRefused as exc:
            return Response({'error': exc.message}, status=exc.status)
        return Response(ok(value), status=status.HTTP_200_OK)

    @extend_schema(tags=_TAGS, request=None, responses={204: None, 404: _ERROR, 409: _ERROR})
    def destroy(self, request, *args, **kwargs):
        """Delete the version, its jobs, shots, frames and files (FR-SS-7).

        Returns:
            204; 404 for an unknown version; 409 while a render of it runs or
            a video or sound file of it is being built.
        """
        response = self._run(delete_submission, lambda _value: None)
        if response.status_code == status.HTTP_200_OK:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return response

    @extend_schema(tags=_TAGS, request=None,
                   responses=_responses('SequenceCancel', dict(_STATE, canceled=serializers.IntegerField()), 404, 409))
    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        """Cancel the version's queued and running pieces and audio jobs (FR-SS-6).

        The shared shots job and pilot are left alone.  Returns 200
        ``{"status", "canceled"}``; 404 for an unknown version; 409 when it
        has already finished.
        """
        return self._run(cancel_submission, lambda value: {'status': value[0], 'canceled': value[1]})

    @extend_schema(tags=_TAGS, request=None,
                   responses=_responses('SequenceRerender', dict(_STATE, requeued=serializers.IntegerField()), 404, 409))
    @action(detail=True, methods=['post'], url_path=r'shots/(?P<index>[0-9]{1,6})/rerender')
    def rerender(self, request, pk=None, index=None):
        """Render a failed or canceled shot again (FR-SS-5, Q9).

        Returns 200 ``{"status", "requeued"}``; 404 for an unknown version or
        shot; 409 for a canceled version or a shot that did not fail.
        """
        return self._run(rerender_shot, lambda value: {'status': value[0], 'requeued': value[1]}, int(index))

    @extend_schema(tags=_TAGS, request=None,
                   responses=_responses('SequenceRetrySound', {'requeued': serializers.IntegerField()}, 404, 409))
    @action(detail=True, methods=['post'], url_path='retry-sound')
    def retry_sound(self, request, pk=None):
        """Requeue the version's failed or canceled audio jobs and clear their sound items (FR-SV-8).

        Returns 200 ``{"requeued": n}``; 404 for an unknown version; 409 when
        the version was canceled or no sound render of it failed.
        """
        return self._run(retry_sound, lambda count: {'requeued': count})

    @extend_schema(tags=_TAGS, request=None,
                   responses=_responses('SequenceRetryVideo', {'reset': serializers.IntegerField()}, 404, 409))
    @action(detail=True, methods=['post'], url_path='retry-video')
    def retry_video(self, request, pk=None):
        """Build the version's failed videos, sound cuts and merged copies again (FR-SV-8).

        Returns 200 ``{"reset": n}``; 404 for an unknown version; 409 when
        the version was canceled or no build of it failed.
        """
        return self._run(retry_video, lambda count: {'reset': count})

    @extend_schema(tags=_TAGS, request=None,
                   responses=_responses('SequenceMergeSound', _STATE, 400, 404, 409))
    @action(detail=True, methods=['post'], url_path='merge-sound')
    def merge_sound(self, request, pk=None):
        """Merge the sound into the shot videos and the whole video (FR-SV-6).

        Returns 200 ``{"status": <version status>}``; 400 for a version
        without video or sound; 404 for an unknown version; 409 when it was
        canceled.
        """
        return self._run(merge_sound, lambda state: {'status': state})
