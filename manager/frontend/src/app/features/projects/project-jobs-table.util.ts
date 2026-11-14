// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Job } from '../../core/services/job.service';
import { TiledJob } from '../../core/services/tiled-job.service';
import { Animation } from '../../core/services/animation.service';

export interface JobTableRow {
  id: number | string;
  name: string;
  type: 'single' | 'tiled' | 'animation';
  status: string;
  is_paused: boolean;
  completed: number | null;
  total: number | null;
  progressUnit: 'frames' | 'tiles' | null;
  time: string;
  createdAt: string;
  thumbnail: string | null;
  outputFile: string | null;
  /** Failure reason to show under the status; null when there is nothing to show. */
  errorMessage: string | null;
  thumbError?: boolean;
}

export const STATUS_ICONS: Record<string, string> = {
  QUEUED: 'hourglass_empty',
  RENDERING: 'sync',
  DONE: 'check_circle',
  ERROR: 'error',
  CANCELED: 'cancel',
  ASSEMBLING: 'build',
  PAUSED: 'pause',
};

export function formatTime(seconds: number | null): string {
  if (seconds == null || seconds <= 0) return '--';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

export function isTopLevelJob(job: Job): boolean {
  return job.animation === null && job.tiled_job === null && job.animation_frame === null;
}

/** Returns the trimmed message, or null when it is missing or blank. */
export function nonBlank(message: string | null | undefined): string | null {
  const trimmed = message?.trim();
  return trimmed ? trimmed : null;
}

/** Failed (status ERROR) child jobs, grouped by their parent. */
export interface FailedChildren {
  byTiledJob: Map<string, Job[]>;
  byAnimation: Map<number, Job[]>;
}

/** Groups the ERROR child jobs of tiled jobs and animations in one pass. */
export function groupFailedChildren(jobs: Job[]): FailedChildren {
  const groups: FailedChildren = { byTiledJob: new Map(), byAnimation: new Map() };
  const add = <K>(map: Map<K, Job[]>, key: K, job: Job): void => {
    const list = map.get(key);
    if (list) list.push(job); else map.set(key, [job]);
  };
  for (const j of jobs) {
    if (j.status !== 'ERROR') continue;
    if (j.tiled_job !== null) add(groups.byTiledJob, j.tiled_job, j);
    if (j.animation !== null) add(groups.byAnimation, j.animation, j);
  }
  return groups;
}

/**
 * Summarises failed child jobs of a parent row, e.g. "2 tiles failed." or
 * "1 frame failed. Walk_Frame_0003: Missing texture". Parent records have no
 * error_message of their own, so the reason comes from the first failed child
 * that has one; the count is shown even when none of them do. Frames are
 * counted by frame number, so failed tiles of one frame in a tiled animation
 * count as one failed frame.
 */
export function childErrorMessage(children: Job[], unit: 'tile' | 'frame'): string | null {
  const failed = children.filter(c => c.status === 'ERROR');
  if (failed.length === 0) return null;
  const count = unit === 'frame' ? new Set(failed.map(c => c.start_frame)).size : failed.length;
  const summary = `${count} ${unit}${count === 1 ? '' : 's'} failed.`;
  const first = failed.find(c => nonBlank(c.error_message) !== null);
  return first ? `${summary} ${first.name}: ${nonBlank(first.error_message)}` : summary;
}

export function mapJobToRow(j: Job): JobTableRow {
  const displayStatus = (j.status === 'QUEUED' && j.is_paused) ? 'PAUSED' : j.status;
  return {
    id: j.id, name: j.name, type: 'single', status: displayStatus,
    is_paused: j.is_paused,
    completed: null, total: null, progressUnit: null,
    time: formatTime(j.render_time_seconds), createdAt: j.submitted_at,
    thumbnail: j.thumbnail, outputFile: j.output_file,
    errorMessage: j.status === 'ERROR' ? nonBlank(j.error_message) : null,
  };
}

export function mapTiledJobToRow(t: TiledJob, failedTiles: Job[] = []): JobTableRow {
  return {
    id: t.id, name: t.name, type: 'tiled', status: t.status, is_paused: false,
    completed: t.completed_tiles, total: t.total_tiles, progressUnit: 'tiles',
    time: formatTime(t.total_render_time_seconds), createdAt: t.submitted_at,
    thumbnail: t.thumbnail, outputFile: t.output_file,
    errorMessage: childErrorMessage(failedTiles, 'tile'),
  };
}

export function mapAnimationToRow(a: Animation, failedJobs: Job[] = []): JobTableRow {
  return {
    id: a.id, name: a.name, type: 'animation', status: a.status, is_paused: false,
    completed: a.completed_frames, total: a.total_frames, progressUnit: 'frames',
    time: formatTime(a.total_render_time_seconds), createdAt: a.submitted_at,
    thumbnail: a.thumbnail, outputFile: null,
    errorMessage: childErrorMessage(failedJobs, 'frame'),
  };
}

export function progressPercent(row: JobTableRow): number {
  if (row.total == null || row.completed == null) return 0;
  if (row.total <= 0) return 0;
  return (row.completed / row.total) * 100;
}
