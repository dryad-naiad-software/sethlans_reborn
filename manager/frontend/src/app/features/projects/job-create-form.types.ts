// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

export type RenderType = 'single' | 'tiled' | 'animation';

export interface JobPrefillData {
  renderType: RenderType;
  renderEngine?: string;
  renderDevice?: string;
  samples?: number;
  resolutionX?: number;
  resolutionY?: number;
  frame?: number;
  tilingConfig?: string;
  startFrame?: number;
  endFrame?: number;
  frameStep?: number;
  animTilingConfig?: string;
}

export interface JobCreateDialogData {
  projectId: string;
  assetId: number;
  prefill?: JobPrefillData;
}
