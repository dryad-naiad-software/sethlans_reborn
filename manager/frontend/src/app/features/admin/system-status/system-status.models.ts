// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

/**
 * Detailed FFmpeg status block. Re-exported here so the system-status feature
 * does not depend on the core service file directly when consuming the type.
 *
 * The shape mirrors the manager-side FFmpegStatusAdminSerializer.
 */
export interface FFmpegDetails {
  source: 'system' | 'bundled';
  version: string;
  path: string;
  status: 'ready' | 'installing' | 'failed';
  error: string | null;
}

/**
 * Generic "part" abstraction — today only FFmpeg is tracked, but the page is
 * designed to render N parts as a list of `<mat-card>`s.
 */
export interface Part {
  /** Display name for the part (e.g. "FFmpeg"). */
  name: string;
  /** Part-specific detail rows: source, version, path, status, error. */
  details: FFmpegDetails;
}
