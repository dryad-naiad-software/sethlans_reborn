// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { SequenceFormValues } from './sequence-submit-payload.util';
import { submissionProblems } from './sequence-submit-validation.util';
import { discovery } from './sequence.fixtures';
import { PresetFacts } from '../../core/services/sequence.types';

function values(overrides: Partial<SequenceFormValues> = {}): SequenceFormValues {
  return {
    asset: 5, map: 'm', sequence: 's', preset: 'p', selection: 'all', frameStart: null, frameEnd: null,
    resolutionX: null, resolutionY: null, frameFormat: 'PNG', videoPreset: '', sound: false, mergeSound: false,
    draft: false, temporalSamples: null, spatialSamples: null, screenPercentage: null, motionBlurOff: false,
    heavyEffectsOff: false, ...overrides,
  };
}

describe('submissionProblems', () => {
  it('finds nothing in plain values', () => {
    expect(submissionProblems(values(), discovery())).toEqual([]);
  });

  it('refuses fractional numbers in the fields in use', () => {
    expect(submissionProblems(values({ resolutionX: 1920.5, resolutionY: 1080 }), null))
      .toEqual(['Width must be a whole number.']);
    expect(submissionProblems(values({ selection: 'range', frameStart: 1.5, frameEnd: 10 }), null))
      .toEqual(['First frame must be a whole number.']);
    expect(submissionProblems(values({ draft: true, temporalSamples: 2.5, screenPercentage: 50.1 }), null))
      .toEqual(['Temporal samples must be a whole number.', 'Screen percentage must be a whole number.']);
    expect(submissionProblems(values({ frameStart: 1.5, temporalSamples: 2.5 }), null)).toEqual([]);
  });

  it('refuses an odd video size, given or the preset\'s', () => {
    const odd = 'Video needs an even width and height; the output is 1921x1080.';
    expect(submissionProblems(values({ videoPreset: 'web_h264', resolutionX: 1921, resolutionY: 1080 }), null))
      .toEqual([odd]);
    expect(submissionProblems(values({ resolutionX: 1921, resolutionY: 1080 }), null)).toEqual([]);
    expect(submissionProblems(values({ videoPreset: 'web_h264', frameFormat: 'EXR', resolutionX: 1921, resolutionY: 1080 }), null))
      .toEqual([]);
    const preset = discovery({ preset_facts: { ...(discovery().preset_facts as PresetFacts), resolution: [1279, 720] } });
    expect(submissionProblems(values({ videoPreset: 'web_h264' }), preset))
      .toEqual(['Video needs an even width and height; the output is 1279x720.']);
  });

  it('refuses a frame range outside the sequence once the shots are read', () => {
    const outside = 'The frame range is outside the sequence (0-95).';
    expect(submissionProblems(values({ selection: 'range', frameStart: 0, frameEnd: 95 }), discovery())).toEqual([]);
    expect(submissionProblems(values({ selection: 'range', frameStart: 10, frameEnd: 96 }), discovery()))
      .toEqual([outside]);
    expect(submissionProblems(values({ selection: 'range', frameStart: 10, frameEnd: 96 }), discovery({ status: 'RUNNING' })))
      .toEqual([]);
  });

  it('refuses draft samples above the preset\'s own', () => {
    expect(submissionProblems(values({ draft: true, temporalSamples: 8, spatialSamples: 1 }), discovery())).toEqual([]);
    expect(submissionProblems(values({ draft: true, temporalSamples: 9, spatialSamples: 2 }), discovery())).toEqual([
      'Temporal samples can be at most 8, the preset\'s value.',
      'Spatial samples can be at most 1, the preset\'s value.',
    ]);
    expect(submissionProblems(values({ draft: true, temporalSamples: 99 }), null)).toEqual([]);
  });

  it('reads empty preset facts ({}) as unknown', () => {
    const empty = discovery({ preset_facts: {} });
    expect(submissionProblems(values({
      draft: true, temporalSamples: 99, spatialSamples: 99, videoPreset: 'web_h264',
    }), empty)).toEqual([]);
  });
});
