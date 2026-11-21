// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { FINAL_SEQUENCE_STATUSES, SequenceShot, SequenceSubmission } from '../../core/services/sequence.types';

/** The manager's prefix of a sound item that a failed sound render set ERROR (`SOUND_RENDER_PREFIX`). */
export const SOUND_RENDER_PREFIX = 'The sound render ';

type Items = Pick<SequenceShot, 'video_status' | 'sound_status' | 'sound_error' | 'sound_video_status'>;

export function isActive(v: SequenceSubmission): boolean {
  return !FINAL_SEQUENCE_STATUSES.has(v.status);
}

function open(v: SequenceSubmission): boolean {
  return v.status !== 'CANCELED' && !v.cancel_requested;
}

function items(v: SequenceSubmission, shots: SequenceShot[] | undefined): Items[] {
  return [v, ...(shots ?? [])];
}

/** `retry-sound/`: a sound item that a failed sound render set ERROR (the version or a shot). */
export function canRetrySound(v: SequenceSubmission, shots?: SequenceShot[]): boolean {
  return open(v) && v.sound && items(v, shots).some(
    i => i.sound_status === 'ERROR' && i.sound_error.startsWith(SOUND_RENDER_PREFIX));
}

/** `retry-video/`: a video, merged copy or sound cut whose build failed (the version or a shot). */
export function canRetryVideo(v: SequenceSubmission, shots?: SequenceShot[]): boolean {
  return open(v) && items(v, shots).some(i =>
    i.video_status === 'ERROR' || i.sound_video_status === 'ERROR'
    || (i.sound_status === 'ERROR' && !i.sound_error.startsWith(SOUND_RENDER_PREFIX)));
}

/** `merge-sound/`: a version with video and sound that does not merge yet, past reading its shots. */
export function canMergeSound(v: SequenceSubmission): boolean {
  return open(v) && v.status !== 'DISCOVERING' && v.sound && v.video_settings !== null && !v.merge_sound;
}

/** Whether a version's shot items are needed to decide its retry buttons. */
export function needsShots(v: SequenceSubmission): boolean {
  return open(v) && (v.sound || v.video_settings !== null);
}
