// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import {
  CreateSubmissionRequest, SequenceDraft, SequenceVideoSettings,
} from '../../core/services/sequence.types';

/** The submit dialog's raw values (FR-SU-2). */
export interface SequenceFormValues {
  asset: number;
  map: string;
  sequence: string;
  preset: string;
  selection: 'all' | 'shots' | 'range';
  frameStart: number | null;
  frameEnd: number | null;
  resolutionX: number | null;
  resolutionY: number | null;
  frameFormat: 'PNG' | 'EXR';
  /** '' for no video, else a video preset key. */
  videoPreset: string;
  sound: boolean;
  mergeSound: boolean;
  draft: boolean;
  temporalSamples: number | null;
  spatialSamples: number | null;
  screenPercentage: number | null;
  motionBlurOff: boolean;
  heavyEffectsOff: boolean;
}

function isNumber(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/** FR-SQ-7 draft: only the keys the person set; null when none. */
export function buildDraft(v: SequenceFormValues): SequenceDraft | null {
  if (!v.draft) return null;
  const draft: SequenceDraft = {};
  if (isNumber(v.temporalSamples)) draft.temporal_samples = v.temporalSamples;
  if (isNumber(v.spatialSamples)) draft.spatial_samples = v.spatialSamples;
  if (isNumber(v.screenPercentage)) draft.screen_percentage = v.screenPercentage;
  if (v.motionBlurOff) draft.motion_blur = false;
  if (v.heavyEffectsOff) {
    draft.ray_traced_shadows = false;
    draft.ray_traced_reflections = false;
  }
  return Object.keys(draft).length > 0 ? draft : null;
}

/** The `POST /api/sequence-submissions/` body from the dialog's values and ticked shots. */
export function buildSubmissionPayload(v: SequenceFormValues, shots: string[]): CreateSubmissionRequest {
  const video: SequenceVideoSettings | null =
    v.frameFormat === 'PNG' && v.videoPreset ? { preset: v.videoPreset } : null;
  const body: CreateSubmissionRequest = {
    asset: v.asset, map: v.map.trim(), sequence: v.sequence.trim(), preset: v.preset.trim(),
    frame_format: v.frameFormat, video_settings: video, sound: v.sound,
    merge_sound: v.sound && video !== null && v.mergeSound, draft: buildDraft(v),
  };
  if (v.selection === 'shots') body.shots = [...shots];
  if (v.selection === 'range' && isNumber(v.frameStart) && isNumber(v.frameEnd)) {
    body.frame_start = v.frameStart;
    body.frame_end = v.frameEnd;
  }
  if (isNumber(v.resolutionX) && isNumber(v.resolutionY)) {
    body.resolution_x = v.resolutionX;
    body.resolution_y = v.resolutionY;
  }
  return body;
}
