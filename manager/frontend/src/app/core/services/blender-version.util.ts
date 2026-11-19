// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

/**
 * Display helpers for the Cycles feature set (#244).
 *
 * Blender 5.0 removed Cycles' "feature set" setting, so a job's
 * `cycles_feature_set` only means something for a Cycles job on Blender
 * below 5.0. These helpers decide only whether to show the value a job
 * already has; the frontend offers no feature set choice.
 *
 * The rule mirrors the manager's (`blender_version_tuple` and
 * `blender_has_cycles_feature_set` in
 * `manager/workers/serializers/validation_utils.py`): the decision is made
 * from the version's series, and a version that cannot be parsed counts as
 * still having the feature set, so nothing is hidden on a guess.
 */

/** First Blender `[major, minor]` without the Cycles feature set setting. */
const FEATURE_SET_REMOVED_IN: readonly [number, number] = [5, 0];

/**
 * The manager's version pattern: `major.minor` with an optional patch,
 * surrounding whitespace allowed. A bare major such as `5` does not match.
 */
const VERSION_PATTERN = /^\s*(\d+)\.(\d+)(?:\.\d+)?\s*$/;

/**
 * True when a Blender version still has Cycles' feature set setting.
 *
 * Accepts a series (`4.5`) or a full version (`4.5.16`). Below 5.0 is true,
 * 5.0 and later is false. Anything the manager cannot parse (null,
 * undefined, empty, a bare major such as `5`, or other text) is true, the
 * same answer the manager gives.
 */
export function blenderHasFeatureSet(version: string | null | undefined): boolean {
  const match = typeof version === 'string' ? VERSION_PATTERN.exec(version) : null;
  if (!match) return true;
  const major = Number(match[1]);
  const minor = Number(match[2]);
  const [removedMajor, removedMinor] = FEATURE_SET_REMOVED_IN;
  return major < removedMajor || (major === removedMajor && minor < removedMinor);
}

/**
 * The fields that decide the feature set display. Job, TiledJob and
 * Animation all have them.
 */
export interface FeatureSetSource {
  render_engine: string;
  effective_blender_version: { series: string; resolved_version: string } | null;
}

/**
 * True when a job's Cycles feature set should be shown: a Cycles job whose
 * Blender series still has the setting (see `blenderHasFeatureSet`).
 *
 * A job without a Blender version (`effective_blender_version` null or
 * undefined, as on Unreal jobs) is handled here, not in
 * `blenderHasFeatureSet`: it has no Blender and therefore no feature set,
 * so the row is hidden. Eevee and Workbench jobs are hidden too.
 */
export function showsCyclesFeatureSet(job: FeatureSetSource): boolean {
  if (job.render_engine !== 'CYCLES') return false;
  const version = job.effective_blender_version;
  if (version == null) return false;
  return blenderHasFeatureSet(version.series);
}
