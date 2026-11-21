// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Downloads, SequenceDiscovery, SequenceShot, SequenceSubmission } from '../../core/services/sequence.types';

/** Test fixtures in the API's shape (sequence submission and discovery serializers). */

/** Text a project could hold; it must reach the page as text, never as markup. */
export const HOSTILE = '<img src=x onerror="window.__xss=1"><b>bold</b>';

export const NO_DOWNLOADS: Downloads = { video: false, video_sound: false, sound: false, frames: false };
export const ALL_DOWNLOADS: Downloads = { video: true, video_sound: true, sound: true, frames: true };

export function shot(overrides: Partial<SequenceShot> = {}): SequenceShot {
  return {
    index: 1, name: 'sh010', start_frame: 0, end_frame: 48, status: 'DONE', error_message: '',
    video_status: 'DONE', video_error: '', sound_status: null, sound_error: '',
    sound_video_status: null, sound_video_error: '', downloads: { ...NO_DOWNLOADS, video: true, frames: true },
    ...overrides,
  };
}

export function version(overrides: Partial<SequenceSubmission> = {}): SequenceSubmission {
  return {
    id: 7, project: 'unreal-uuid', asset: 5, map: '/Game/Maps/Main.Main', sequence: '/Game/Seq/Ep1.Ep1',
    preset: '/Game/Presets/Final.Final', version: 2, name: 'Ep1 v2', selected_shots: null,
    frame_start: null, frame_end: null, frame_format: 'PNG', video_settings: { preset: 'web_h264' },
    sound: true, merge_sound: false, status: 'DONE', cancel_requested: false, error_message: '',
    warnings: [], submitted_at: '2026-11-20T00:00:00Z', video_status: 'DONE', video_error: '',
    sound_status: 'DONE', sound_error: '', sound_video_status: null, sound_video_error: '',
    routing_note: null, downloads: { ...ALL_DOWNLOADS, video_sound: false }, shots_total: 2, shots_done: 2,
    shots_error: 0, pieces_total: 3, pieces_done: 3,
    ...overrides,
  };
}

export function discovery(overrides: Partial<SequenceDiscovery> = {}): SequenceDiscovery {
  return {
    id: 3, project: 'unreal-uuid', asset: 5, map: '/Game/Maps/Main.Main', sequence: '/Game/Seq/Ep1.Ep1',
    preset: '/Game/Presets/Final.Final', status: 'DONE', playback_start: 0, playback_end: 96,
    preset_facts: {
      resolution: [2560, 1440], temporal_samples: 8, spatial_samples: 1,
      can_png: true, can_exr: true, can_sound: true,
    },
    shots: [
      { index: 1, name: 'sh010', start: 0, end: 48 },
      { index: 2, name: 'sh020', start: 48, end: 96 },
    ],
    warnings: [], error_message: '',
    ...overrides,
  };
}

export function text(el: Element | null | undefined): string {
  return (el?.textContent ?? '').replace(/\s+/g, ' ').trim();
}
