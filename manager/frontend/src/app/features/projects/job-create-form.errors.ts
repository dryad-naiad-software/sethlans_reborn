// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

/**
 * Error-message parser for the job-create form.
 *
 * Translates DRF validation error bodies into user-facing snackbar
 * strings.  Recognizes:
 *
 *  - ``name`` / ``detail`` / ``non_field_errors`` — generic DRF fields.
 *  - ``video_settings`` with closed-vocabulary codes
 *    ``video_assembly_unavailable`` (spec FR §131) and
 *    ``video_settings_immutable`` (spec FR §137).  The contract is the
 *    code, not the prose — matching is on the literal string in the
 *    array, NOT on a translated message.
 *
 * Falls through to a generic ``Failed to create job`` message for
 * unrecognized shapes.
 */

const VIDEO_ASSEMBLY_UNAVAILABLE_MSG =
  'Video assembly is preparing — refresh in a moment, ' +
  'or skip the video output for now.';

const VIDEO_SETTINGS_IMMUTABLE_MSG =
  "Video settings can't be changed after the animation is created.";

export function parseJobCreateError(
  errorBody: Record<string, unknown> | undefined,
): string {
  const nameMsg = (errorBody?.['name'] as string[] | undefined)?.[0];
  if (nameMsg) return nameMsg;

  const detailMsg = errorBody?.['detail'] as string | undefined;
  if (detailMsg) return detailMsg;

  const nonFieldMsg =
    (errorBody?.['non_field_errors'] as string[] | undefined)?.[0];
  if (nonFieldMsg) return nonFieldMsg;

  const videoErrors = errorBody?.['video_settings'];
  if (Array.isArray(videoErrors) && videoErrors.length > 0) {
    if (videoErrors.includes('video_assembly_unavailable')) {
      return VIDEO_ASSEMBLY_UNAVAILABLE_MSG;
    }
    if (videoErrors.includes('video_settings_immutable')) {
      return VIDEO_SETTINGS_IMMUTABLE_MSG;
    }
    return `Video settings rejected: ${videoErrors[0]}`;
  }

  return 'Failed to create job';
}
