// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Failure-category copy tables for the FFmpeg installer page (FR-M2-7).
//
// Pulled out of ``ffmpeg.js`` to keep that controller under the
// 250-line cap after issue #182 added the bfcache pageshow path and
// the consecutive-poll-failure tracking. The tables are pure data —
// no behaviour — so a separate import keeps the controller focused on
// state-machine logic.

export const FAILURE_HEADLINES = {
  download_failed: 'Could not download FFmpeg.',
  sha_mismatch: 'FFmpeg download verification failed.',
  version_mismatch: 'FFmpeg installed but reports an unexpected version.',
  extraction_failed: 'FFmpeg archive could not be extracted.',
  network_error: 'Network error during download.',
  generic: 'FFmpeg installation failed.',
};

export const FAILURE_BODIES = {
  download_failed: 'Check your network connection and try again.',
  sha_mismatch:
    'The file does not match the expected checksum. '
    + 'Setup cannot proceed safely.',
  version_mismatch:
    'Setup cannot proceed without the expected FFmpeg version.',
  extraction_failed:
    'The download finished but the archive contents were unsafe or corrupt.',
  network_error: 'A network error occurred during the download.',
  generic: 'Check the launcher logs for the underlying error.',
};

// Categories the user is allowed to re-attempt automatically. The
// integrity-failure categories (sha_mismatch, version_mismatch) are
// excluded by design — they imply the wizard's own pinned download
// metadata is wrong, so re-running the same fetch would just fail the
// same way.
export const RETRY_ALLOWED = new Set([
  'download_failed',
  'extraction_failed',
  'network_error',
  'generic',
]);
