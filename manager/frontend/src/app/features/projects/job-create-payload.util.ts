// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import {
  generateOutputFilePattern, buildRenderSettings, buildTiledRenderSettings,
  parseTilingConfig,
} from './render-payload.util';
import { CreateJobRequest } from '../../core/services/job.service';
import { CreateTiledJobRequest } from '../../core/services/tiled-job.service';
import { CreateAnimationRequest } from '../../core/services/animation.service';

interface FormValues {
  name: string | null;
  renderEngine: string | null;
  renderDevice: string | null;
  samples: number | null;
  resolutionX: number | null;
  resolutionY: number | null;
  outputFormat: string | null;
  jpegQuality: number | null;
  colorDepth: string | null;
  frame: number | null;
  tilingConfig: string | null;
  startFrame: number | null;
  endFrame: number | null;
  frameStep: number | null;
  animTilingConfig: string | null;
  generateVideo: boolean | null;
  videoPreset: string | null;
  videoFramerate: number | null;
  videoContainer: string | null;
  videoCodec: string | null;
  videoCrf: number | null;
}

/** Build the API payload for a single render job. */
export function buildSingleJobPayload(
  v: FormValues, assetId: number,
): CreateJobRequest {
  return {
    name: v.name!, asset_id: assetId,
    output_file_pattern: generateOutputFilePattern(v.name!, v.outputFormat!),
    start_frame: v.frame!, end_frame: v.frame!,
    render_engine: v.renderEngine!, render_device: v.renderDevice!,
    render_settings: buildRenderSettings(
      v.samples!, v.resolutionX!, v.resolutionY!,
      v.outputFormat!, v.jpegQuality!, v.colorDepth!,
    ),
  };
}

/** Build the API payload for a tiled render job. */
export function buildTiledJobPayload(
  v: FormValues, projectId: string, assetId: number,
): CreateTiledJobRequest {
  const t = parseTilingConfig(v.tilingConfig!);
  return {
    name: v.name!, project: projectId, asset_id: assetId,
    final_resolution_x: v.resolutionX!, final_resolution_y: v.resolutionY!,
    tile_count_x: t.tile_count_x, tile_count_y: t.tile_count_y,
    render_engine: v.renderEngine!, render_device: v.renderDevice!,
    render_settings: buildTiledRenderSettings(v.samples!, v.outputFormat!, v.jpegQuality!),
  };
}

/** Build the API payload for an animation job. */
export function buildAnimationPayload(
  v: FormValues, projectId: string, assetId: number,
): CreateAnimationRequest {
  const payload: CreateAnimationRequest = {
    name: v.name!, project: projectId, asset_id: assetId,
    output_file_pattern: generateOutputFilePattern(v.name!, v.outputFormat!),
    start_frame: v.startFrame!, end_frame: v.endFrame!, frame_step: v.frameStep!,
    tiling_config: v.animTilingConfig!,
    render_engine: v.renderEngine!, render_device: v.renderDevice!,
    render_settings: buildRenderSettings(
      v.samples!, v.resolutionX!, v.resolutionY!,
      v.outputFormat!, v.jpegQuality!, v.colorDepth!,
    ),
  };
  if (v.generateVideo) {
    payload.video_settings = {
      preset: v.videoPreset!, container: v.videoContainer!,
      codec: v.videoCodec!, framerate: v.videoFramerate!, crf: v.videoCrf!,
    };
  }
  return payload;
}
