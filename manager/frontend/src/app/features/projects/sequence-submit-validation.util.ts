// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { SequenceDiscovery } from '../../core/services/sequence.types';
import { SequenceFormValues } from './sequence-submit-payload.util';

const WHOLE_NUMBER_FIELDS: [keyof SequenceFormValues, string][] = [
  ['frameStart', 'First frame'], ['frameEnd', 'Last frame'], ['resolutionX', 'Width'], ['resolutionY', 'Height'],
  ['temporalSamples', 'Temporal samples'], ['spatialSamples', 'Spatial samples'],
  ['screenPercentage', 'Screen percentage'],
];

function set(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/**
 * What the API would refuse in the dialog's values (FR-SQ-3, FR-SQ-5,
 * FR-SQ-7): fractional numbers (strict integers), an odd video size, a
 * frame range outside the sequence, and draft samples above the preset's.
 * The rules that need the shots apply only to a DONE discovery.
 */
export function submissionProblems(v: SequenceFormValues, discovery: SequenceDiscovery | null): string[] {
  const problems: string[] = [];
  const range = v.selection === 'range';
  for (const [key, label] of WHOLE_NUMBER_FIELDS) {
    const value = v[key];
    const used = key.startsWith('frame') ? range : (key.endsWith('Samples') || key === 'screenPercentage') ? v.draft : true;
    if (used && set(value) && !Number.isInteger(value)) problems.push(`${label} must be a whole number.`);
  }
  const done = discovery?.status === 'DONE' ? discovery : null;
  const facts = done?.preset_facts ?? null;

  const video = v.frameFormat === 'PNG' && v.videoPreset !== '';
  const size: [number, number] | null = set(v.resolutionX) && set(v.resolutionY)
    ? [v.resolutionX, v.resolutionY] : facts?.resolution ?? null;
  if (video && size && (size[0] % 2 !== 0 || size[1] % 2 !== 0)) {
    problems.push(`Video needs an even width and height; the output is ${size[0]}x${size[1]}.`);
  }

  const start = done?.playback_start;
  const end = done?.playback_end;
  if (range && set(start) && set(end) && set(v.frameStart) && set(v.frameEnd)
      && (v.frameStart < start || v.frameEnd > end - 1)) {
    problems.push(`The frame range is outside the sequence (${start}-${end - 1}).`);
  }

  if (v.draft && facts) {
    if (set(v.temporalSamples) && set(facts.temporal_samples) && v.temporalSamples > facts.temporal_samples) {
      problems.push(`Temporal samples can be at most ${facts.temporal_samples}, the preset's value.`);
    }
    if (set(v.spatialSamples) && set(facts.spatial_samples) && v.spatialSamples > facts.spatial_samples) {
      problems.push(`Spatial samples can be at most ${facts.spatial_samples}, the preset's value.`);
    }
  }
  return problems;
}
