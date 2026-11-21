// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

/**
 * Shapes of the Unreal sequence API (`/api/sequence-discoveries/`,
 * `/api/sequence-submissions/`), as the manager's serializers return them.
 */

export type DiscoveryStatus = 'QUEUED' | 'RUNNING' | 'DONE' | 'ERROR';
export type SequenceStatus =
  'DISCOVERING' | 'QUEUED' | 'RENDERING' | 'ASSEMBLING' | 'DONE' | 'PARTLY_DONE' | 'ERROR' | 'CANCELED';
export type ShotStatus = 'PENDING' | 'RENDERING' | 'DONE' | 'ERROR' | 'CANCELED';
export type ItemStatus = 'PENDING' | 'ASSEMBLING' | 'DONE' | 'ERROR' | null;

/** A DRF page (the sequence lists are paginated). */
export interface Page<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface PresetFacts {
  resolution: [number, number] | null;
  temporal_samples: number;
  spatial_samples: number;
  can_png: boolean;
  can_exr: boolean;
  can_sound: boolean;
}

export interface DiscoveredShot {
  index: number;
  name: string;
  start: number;
  /** Exclusive. */
  end: number;
}

export interface SequenceDiscovery {
  id: number;
  project: string;
  asset: number;
  map: string;
  sequence: string;
  preset: string;
  status: DiscoveryStatus;
  playback_start: number | null;
  /** Exclusive. */
  playback_end: number | null;
  /** `{}` until the shots are read (the API never sends null for it); read every key as maybe missing. */
  preset_facts: PresetFacts | Record<string, never> | null;
  /** Only in the detail view. */
  shots?: DiscoveredShot[] | null;
  warnings?: string[];
  error_message: string;
}

export interface Downloads {
  video: boolean;
  video_sound: boolean;
  sound: boolean;
  frames: boolean;
}

export interface SequenceShot {
  index: number;
  name: string;
  start_frame: number;
  /** Exclusive. */
  end_frame: number;
  status: ShotStatus;
  error_message: string;
  video_status: ItemStatus;
  video_error: string;
  sound_status: ItemStatus;
  sound_error: string;
  sound_video_status: ItemStatus;
  sound_video_error: string;
  downloads: Downloads;
}

export interface SequenceVideoSettings {
  preset: string;
  container?: string;
  codec?: string;
  crf?: number;
}

export interface SequenceSubmission {
  id: number;
  project: string;
  asset: number;
  map: string;
  sequence: string;
  preset: string;
  version: number;
  name: string;
  selected_shots: string[] | null;
  frame_start: number | null;
  frame_end: number | null;
  frame_format: 'PNG' | 'EXR';
  video_settings: SequenceVideoSettings | null;
  sound: boolean;
  merge_sound: boolean;
  status: SequenceStatus;
  cancel_requested: boolean;
  error_message: string;
  warnings: string[];
  submitted_at: string;
  video_status: ItemStatus;
  video_error: string;
  sound_status: ItemStatus;
  sound_error: string;
  sound_video_status: ItemStatus;
  sound_video_error: string;
  routing_note: string | null;
  downloads: Downloads;
  shots_total: number;
  shots_done: number;
  shots_error: number;
  pieces_total: number;
  pieces_done: number;
  /** Only in the detail view. */
  shots?: SequenceShot[];
}

export interface SequenceDraft {
  temporal_samples?: number;
  spatial_samples?: number;
  screen_percentage?: number;
  motion_blur?: false;
  ray_traced_shadows?: false;
  ray_traced_reflections?: false;
}

export interface DiscoveryKey {
  asset: number;
  map: string;
  sequence: string;
  preset: string;
}

export interface CreateSubmissionRequest extends DiscoveryKey {
  shots?: string[];
  frame_start?: number;
  frame_end?: number;
  resolution_x?: number;
  resolution_y?: number;
  frame_format: 'PNG' | 'EXR';
  video_settings: SequenceVideoSettings | null;
  sound: boolean;
  merge_sound: boolean;
  draft: SequenceDraft | null;
}

/** States in which nothing more happens to a version without an action. */
export const FINAL_SEQUENCE_STATUSES: ReadonlySet<SequenceStatus> =
  new Set<SequenceStatus>(['DONE', 'PARTLY_DONE', 'ERROR', 'CANCELED']);

export const SEQUENCE_STATUS_LABELS: Record<SequenceStatus, string> = {
  DISCOVERING: 'Reading shots', QUEUED: 'Queued', RENDERING: 'Rendering', ASSEMBLING: 'Assembling',
  DONE: 'Done', PARTLY_DONE: 'Partly done', ERROR: 'Error', CANCELED: 'Canceled',
};
