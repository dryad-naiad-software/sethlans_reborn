// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { SequenceFormValues, buildDraft, buildSubmissionPayload } from './sequence-submit-payload.util';

function values(overrides: Partial<SequenceFormValues> = {}): SequenceFormValues {
  return {
    asset: 5, map: ' /Game/M.M ', sequence: '/Game/S.S', preset: '/Game/P.P', selection: 'all',
    frameStart: null, frameEnd: null, resolutionX: null, resolutionY: null, frameFormat: 'PNG',
    videoPreset: '', sound: false, mergeSound: false, draft: false, temporalSamples: null,
    spatialSamples: null, screenPercentage: null, motionBlurOff: false, heavyEffectsOff: false,
    ...overrides,
  };
}

describe('buildSubmissionPayload', () => {
  it('sends the whole sequence with the preset size and no options', () => {
    expect(buildSubmissionPayload(values(), [])).toEqual({
      asset: 5, map: '/Game/M.M', sequence: '/Game/S.S', preset: '/Game/P.P', frame_format: 'PNG',
      video_settings: null, sound: false, merge_sound: false, draft: null,
    });
  });

  it('sends the ticked shots, or the frame range, never both', () => {
    const shots = buildSubmissionPayload(values({ selection: 'shots', frameStart: 1, frameEnd: 9 }), ['a', 'b']);
    expect(shots.shots).toEqual(['a', 'b']);
    expect(shots.frame_start).toBeUndefined();
    const range = buildSubmissionPayload(values({ selection: 'range', frameStart: 10, frameEnd: 20 }), ['a']);
    expect(range.shots).toBeUndefined();
    expect([range.frame_start, range.frame_end]).toEqual([10, 20]);
  });

  it('sends a resolution only as a pair', () => {
    expect(buildSubmissionPayload(values({ resolutionX: 1920 }), []).resolution_x).toBeUndefined();
    const both = buildSubmissionPayload(values({ resolutionX: 1920, resolutionY: 1080 }), []);
    expect([both.resolution_x, both.resolution_y]).toEqual([1920, 1080]);
  });

  it('sends the video preset without a frame rate, and none for EXR', () => {
    expect(buildSubmissionPayload(values({ videoPreset: 'web_h264' }), []).video_settings)
      .toEqual({ preset: 'web_h264' });
    expect(buildSubmissionPayload(values({ videoPreset: 'web_h264', frameFormat: 'EXR' }), []).video_settings)
      .toBeNull();
  });

  it('merges sound only with sound and a video', () => {
    expect(buildSubmissionPayload(values({ mergeSound: true }), []).merge_sound).toBeFalse();
    expect(buildSubmissionPayload(values({ mergeSound: true, sound: true }), []).merge_sound).toBeFalse();
    expect(buildSubmissionPayload(values({ mergeSound: true, sound: true, videoPreset: 'web_h264' }), []).merge_sound)
      .toBeTrue();
  });
});

describe('buildDraft', () => {
  it('is null when draft is off or nothing is set', () => {
    expect(buildDraft(values({ temporalSamples: 2 }))).toBeNull();
    expect(buildDraft(values({ draft: true }))).toBeNull();
  });

  it('sends only the set keys; heavy effects off turns off both ray-traced switches', () => {
    expect(buildDraft(values({
      draft: true, temporalSamples: 2, spatialSamples: 1, screenPercentage: 50, motionBlurOff: true,
      heavyEffectsOff: true,
    }))).toEqual({
      temporal_samples: 2, spatial_samples: 1, screen_percentage: 50, motion_blur: false,
      ray_traced_shadows: false, ray_traced_reflections: false,
    });
    expect(buildDraft(values({ draft: true, motionBlurOff: true }))).toEqual({ motion_blur: false });
  });
});
