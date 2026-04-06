// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

declare module 'tga-js' {
  export default class TgaLoader {
    header: { width: number; height: number };
    load(data: Uint8Array): void;
    getCanvas(): HTMLCanvasElement;
    getImageData(imageData?: ImageData): ImageData;
  }
}
