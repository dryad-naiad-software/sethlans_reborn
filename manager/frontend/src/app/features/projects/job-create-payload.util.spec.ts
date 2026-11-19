// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import {
  buildSingleJobPayload, buildTiledJobPayload, buildAnimationPayload,
} from './job-create-payload.util';

/**
 * Issue #244: pins the current create-job payloads. The create forms offer no
 * Cycles feature set today, so none is sent and the manager's default applies.
 * A later 4.x-only feature set selector would be a legitimate change to this.
 */

type FormValues = Parameters<typeof buildSingleJobPayload>[0];

function formValues(overrides: Partial<FormValues> = {}): FormValues {
  return {
    name: 'Shot 010', renderEngine: 'CYCLES', renderDevice: 'GPU',
    samples: 128, resolutionX: 1920, resolutionY: 1080,
    outputFormat: 'PNG', jpegQuality: 90, colorDepth: '8',
    frame: 1, tilingConfig: '2x2',
    startFrame: 1, endFrame: 10, frameStep: 1, animTilingConfig: '2x2',
    generateVideo: true, videoPreset: 'custom', videoFramerate: 24,
    videoContainer: 'mp4', videoCodec: 'h264', videoCrf: 23,
    ...overrides,
  };
}

/** Every key at any depth of a payload, so nested settings are checked too. */
function allKeys(value: unknown): string[] {
  if (value === null || typeof value !== 'object') return [];
  return Object.entries(value as Record<string, unknown>)
    .flatMap(([key, child]) => [key, ...allKeys(child)]);
}

function expectNoFeatureSet(payload: object): void {
  expect(allKeys(payload).filter(k => k.includes('feature_set'))).toEqual([]);
}

describe('job-create-payload.util (no Cycles feature set sent today, #244)', () => {
  for (const engine of ['CYCLES', 'BLENDER_EEVEE_NEXT', 'WORKBENCH']) {
    describe(`with render engine ${engine}`, () => {
      const v = formValues({ renderEngine: engine });

      it('should build a single job payload without a feature set', () => {
        const payload = buildSingleJobPayload(v, 7);
        expect(payload.render_engine).toBe(engine);
        expectNoFeatureSet(payload);
      });

      it('should build a tiled job payload without a feature set', () => {
        const payload = buildTiledJobPayload(v, 'project-uuid', 7);
        expect(payload.render_engine).toBe(engine);
        expectNoFeatureSet(payload);
      });

      it('should build an animation payload without a feature set', () => {
        const payload = buildAnimationPayload(v, 'project-uuid', 7);
        expect(payload.render_engine).toBe(engine);
        expect(payload.video_settings).toBeDefined();
        expectNoFeatureSet(payload);
      });
    });
  }

  it('should build an animation payload without video settings or a feature set', () => {
    const payload = buildAnimationPayload(
      formValues({ generateVideo: false, animTilingConfig: 'NONE' }), 'project-uuid', 7,
    );
    expect(payload.video_settings).toBeUndefined();
    expectNoFeatureSet(payload);
  });
});
