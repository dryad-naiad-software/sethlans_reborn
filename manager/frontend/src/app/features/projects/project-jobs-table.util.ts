// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Job } from '../../core/services/job.service';

export interface JobTableRow {
  id: number | string;
  name: string;
  type: 'single' | 'tiled' | 'animation';
  status: string;
  worker: string;
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
};

export function formatTime(seconds: number | null): string {
  if (seconds == null || seconds <= 0) return '--';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

export function isTopLevelJob(job: Job): boolean {
  return job.animation === null && job.tiled_job === null && job.animation_frame === null;
}
