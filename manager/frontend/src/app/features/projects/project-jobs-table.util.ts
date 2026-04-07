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

export function mapJobToRow(j: Job): JobTableRow {
  const displayStatus = (j.status === 'QUEUED' && j.is_paused) ? 'PAUSED' : j.status;
  return {
    id: j.id, name: j.name, type: 'single', status: displayStatus,
    is_paused: j.is_paused,
    completed: null, total: null, progressUnit: null,
    time: formatTime(j.render_time_seconds), createdAt: j.submitted_at,
    thumbnail: j.thumbnail, outputFile: j.output_file,
  };
}

export function mapTiledJobToRow(t: TiledJob): JobTableRow {
  return {
    id: t.id, name: t.name, type: 'tiled', status: t.status, is_paused: false,
    completed: t.completed_tiles, total: t.total_tiles, progressUnit: 'tiles',
    time: formatTime(t.total_render_time_seconds), createdAt: t.submitted_at,
    thumbnail: t.thumbnail, outputFile: t.output_file,
  };
}

export function mapAnimationToRow(a: Animation): JobTableRow {
  return {
    id: a.id, name: a.name, type: 'animation', status: a.status, is_paused: false,
    completed: a.completed_frames, total: a.total_frames, progressUnit: 'frames',
    time: formatTime(a.total_render_time_seconds), createdAt: a.submitted_at,
    thumbnail: a.thumbnail, outputFile: null,
  };
}

export function progressPercent(row: JobTableRow): number {
  if (row.total == null || row.completed == null) return 0;
  if (row.total <= 0) return 0;
  return (row.completed / row.total) * 100;
}
