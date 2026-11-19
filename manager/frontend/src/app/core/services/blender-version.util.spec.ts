// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import {
  blenderHasFeatureSet, showsCyclesFeatureSet, FeatureSetSource,
} from './blender-version.util';
import { TiledJob } from './tiled-job.service';
import { Animation } from './animation.service';
import { Project } from './project.service';
import { Asset } from './asset.service';

/**
 * Issue #244: Blender 5.0 removed Cycles' "feature set" setting. The display
 * rule mirrors the manager's: a `major.minor[.patch]` version below 5.0 has
 * it, 5.0 and later do not, and anything unparseable counts as having it.
 * The row is shown only for Cycles jobs that have a Blender version, decided
 * by its series.
 */

type Version = NonNullable<FeatureSetSource['effective_blender_version']>;

function job(engine: string, version: Version | null | undefined): FeatureSetSource {
  return { render_engine: engine, effective_blender_version: version as Version | null };
}

const PROJECT: Project = {
  id: 'project-uuid', name: 'Project', renderer: 'BLENDER', blender_version: 1,
  blender_version_details: {
    id: 1, major: 4, minor: 5, series: '4.5', resolved_version: '4.5.16',
    is_default: true, added_at: '2025-01-01T00:00:00Z', last_patch_check: null,
  },
  engine_build: '', created_at: '2025-06-01T00:00:00Z',
};

const ASSET: Asset = {
  id: 5, name: 'snapshot', blend_file: '/media/scene.blend',
  created_at: '2025-06-01T00:00:00Z', project: PROJECT.id, project_details: PROJECT,
};

describe('blender-version.util', () => {
  describe('blenderHasFeatureSet', () => {
    for (const version of ['4.5.16', '4.5', '4.1', ' 4.2 ', '0.9', '4.99.1']) {
      it(`should return true for Blender ${JSON.stringify(version)} (below 5.0)`, () => {
        expect(blenderHasFeatureSet(version)).toBeTrue();
      });
    }

    for (const version of ['5.0', '5.0.1', '5.2.2', ' 5.0 ', '6.0', '10.1']) {
      it(`should return false for Blender ${JSON.stringify(version)} (5.0 or later)`, () => {
        expect(blenderHasFeatureSet(version)).toBeFalse();
      });
    }

    const unparseable = [
      '', '   ', 'abc', '4.x', 'v4.2', '4.5-beta', '4', '5', '5.0.1.1', '5.', '.5',
    ];
    for (const version of unparseable) {
      it(`should count the unparseable ${JSON.stringify(version)} as having it`, () => {
        expect(blenderHasFeatureSet(version)).toBeTrue();
      });
    }

    it('should count null as having it', () => {
      expect(blenderHasFeatureSet(null)).toBeTrue();
    });

    it('should count undefined as having it', () => {
      expect(blenderHasFeatureSet(undefined)).toBeTrue();
    });
  });

  describe('showsCyclesFeatureSet', () => {
    it('should show the feature set for a Cycles job on Blender 4.2', () => {
      expect(showsCyclesFeatureSet(job('CYCLES', { series: '4.2', resolved_version: '4.2.1' })))
        .toBeTrue();
    });

    it('should hide the feature set for a Cycles job on Blender 5.0', () => {
      expect(showsCyclesFeatureSet(job('CYCLES', { series: '5.0', resolved_version: '5.0.1' })))
        .toBeFalse();
    });

    it('should hide the feature set for an Eevee job on Blender 4.x', () => {
      expect(showsCyclesFeatureSet(
        job('BLENDER_EEVEE_NEXT', { series: '4.2', resolved_version: '4.2.1' }),
      )).toBeFalse();
    });

    it('should hide the feature set for a Workbench job on Blender 4.x', () => {
      expect(showsCyclesFeatureSet(
        job('WORKBENCH', { series: '4.5', resolved_version: '4.5.16' }),
      )).toBeFalse();
    });

    it('should hide the feature set for an Unreal job (null Blender version)', () => {
      // Unreal jobs keep the model's default engine, so the null version decides.
      expect(showsCyclesFeatureSet(job('CYCLES', null))).toBeFalse();
    });

    it('should not throw and should hide the feature set when the version is missing', () => {
      const missing = { render_engine: 'CYCLES' } as unknown as FeatureSetSource;
      expect(() => showsCyclesFeatureSet(missing)).not.toThrow();
      expect(showsCyclesFeatureSet(missing)).toBeFalse();
      expect(showsCyclesFeatureSet(job('CYCLES', undefined))).toBeFalse();
    });

    describe('series decides, resolved_version is not read', () => {
      it('should show the row for series 4.5 even when resolved_version is 5.0.1', () => {
        expect(showsCyclesFeatureSet(job('CYCLES', { series: '4.5', resolved_version: '5.0.1' })))
          .toBeTrue();
      });

      it('should hide the row for series 5.0 even when resolved_version is 4.5.16', () => {
        expect(showsCyclesFeatureSet(job('CYCLES', { series: '5.0', resolved_version: '4.5.16' })))
          .toBeFalse();
      });

      for (const series of ['abc', '']) {
        it(`should show the row for the unparseable series ${JSON.stringify(series)}`, () => {
          expect(showsCyclesFeatureSet(job('CYCLES', { series, resolved_version: '5.0.1' })))
            .toBeTrue();
        });
      }
    });

    describe('other job kinds', () => {
      it('should accept a TiledJob and decide by its series', () => {
        const tiled: TiledJob = {
          id: 'tiled-uuid', name: 'Tiled', status: 'QUEUED', progress: '0/4',
          total_tiles: 4, completed_tiles: 0, project: PROJECT.id, project_details: PROJECT,
          asset: ASSET, final_resolution_x: 1920, final_resolution_y: 1080,
          tile_count_x: 2, tile_count_y: 2, blender_version: null,
          effective_blender_version: { series: '5.0', resolved_version: '5.0.1' },
          render_engine: 'CYCLES', render_device: 'GPU', cycles_feature_set: 'SUPPORTED',
          render_settings: {}, submitted_at: '2025-06-02T00:00:00Z', completed_at: null,
          total_render_time_seconds: 0, output_file: null, thumbnail: null,
        };
        expect(showsCyclesFeatureSet(tiled)).toBeFalse();
        tiled.effective_blender_version = { series: '4.5', resolved_version: '4.5.16' };
        expect(showsCyclesFeatureSet(tiled)).toBeTrue();
      });

      it('should accept an Animation and decide by its series', () => {
        const animation: Animation = {
          id: 3, name: 'Anim', status: 'QUEUED', progress: '0/10',
          total_frames: 10, completed_frames: 0, project: PROJECT.id, project_details: PROJECT,
          asset: ASSET, output_file_pattern: 'frame_####', start_frame: 1, end_frame: 10,
          frame_step: 1, blender_version: null,
          effective_blender_version: { series: '4.5', resolved_version: '4.5.16' },
          render_engine: 'CYCLES', render_device: 'GPU', cycles_feature_set: 'EXPERIMENTAL',
          render_settings: {}, tiling_config: 'NONE', submitted_at: '2025-06-02T00:00:00Z',
          completed_at: null, total_render_time_seconds: 0, thumbnail: null, frames: [],
          video_settings: null, video_status: null, video_file: null, video_error: null,
        };
        expect(showsCyclesFeatureSet(animation)).toBeTrue();
        animation.effective_blender_version = { series: '5.1', resolved_version: '5.1.0' };
        expect(showsCyclesFeatureSet(animation)).toBeFalse();
      });
    });
  });
});
