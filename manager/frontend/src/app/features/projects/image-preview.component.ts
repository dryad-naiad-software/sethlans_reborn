// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import {
  Component, input, signal, ElementRef, viewChild, effect, inject,
} from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatIconModule } from '@angular/material/icon';
import { OUTPUT_FORMATS } from './render-payload.util';

/** Formats the browser can display natively via <img>. */
const NATIVE_FORMATS = new Set(['PNG', 'JPEG', 'BMP']);

@Component({
  selector: 'app-image-preview',
  standalone: true,
  imports: [MatProgressSpinnerModule, MatIconModule],
  template: `
    @if (loading()) {
      <div class="preview-loading"><mat-spinner diameter="40" /></div>
    }
    @if (errorMessage()) {
      <div class="preview-fallback">
        @if (thumbnailSrc()) {
          <img [src]="thumbnailSrc()!" [alt]="alt()" class="preview-img" />
        } @else {
          <div class="no-image"><mat-icon>image</mat-icon><span>No image</span></div>
        }
        <p class="decode-error">{{ errorMessage() }}</p>
      </div>
    } @else if (!loading()) {
      @if (isNative()) {
        @if (src()) {
          <img [src]="src()!" [alt]="alt()" class="preview-img" />
        } @else if (thumbnailSrc()) {
          <img [src]="thumbnailSrc()!" [alt]="alt()" class="preview-img" />
        } @else {
          <div class="no-image"><mat-icon>image</mat-icon><span>No image</span></div>
        }
      } @else if (!canvasReady() && !src()) {
        @if (thumbnailSrc()) {
          <img [src]="thumbnailSrc()!" [alt]="alt()" class="preview-img" />
        } @else {
          <div class="no-image"><mat-icon>image</mat-icon><span>No image</span></div>
        }
      }
    }
    <canvas #previewCanvas [class.hidden]="!canvasReady()" class="preview-canvas"></canvas>
  `,
  styles: [`
    :host { display: block; }
    .preview-loading { display: flex; justify-content: center; padding: 24px; }
    .preview-img { max-width: 100%; max-height: 60vh; border-radius: 4px; }
    .preview-canvas { max-width: 100%; max-height: 60vh; border-radius: 4px; }
    .preview-canvas.hidden { display: none; }
    .no-image {
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      height: 200px; color: rgba(0,0,0,0.3);
    }
    .no-image mat-icon { font-size: 48px; width: 48px; height: 48px; }
    .preview-fallback { text-align: center; }
    .decode-error { font-size: 13px; color: rgba(0,0,0,0.6); margin-top: 8px; }
  `],
})
export class ImagePreviewComponent {
  readonly src = input<string | null>(null);
  readonly alt = input<string>('');
  readonly format = input<string>('PNG');
  readonly thumbnailSrc = input<string | null>(null);

  readonly loading = signal(false);
  readonly errorMessage = signal<string | null>(null);
  readonly canvasReady = signal(false);

  private readonly http = inject(HttpClient);
  readonly previewCanvas = viewChild<ElementRef<HTMLCanvasElement>>('previewCanvas');

  constructor() {
    effect(() => {
      const fmt = this.format();
      const url = this.src();
      this.errorMessage.set(null);
      this.canvasReady.set(false);
      if (!url || NATIVE_FORMATS.has(fmt)) return;
      this.decodeNonNative(url, fmt);
    });
  }

  isNative(): boolean {
    return NATIVE_FORMATS.has(this.format());
  }

  private async decodeNonNative(url: string, fmt: string): Promise<void> {
    this.loading.set(true);
    try {
      const buffer = await this.fetchBlob(url);
      if (fmt === 'TIFF') {
        await this.decodeTiff(buffer);
      } else if (fmt === 'OPEN_EXR' || fmt === 'OPEN_EXR_MULTILAYER') {
        await this.decodeExr(buffer);
      } else if (fmt === 'HDR') {
        await this.decodeHdr(buffer);
      } else if (fmt === 'TARGA') {
        await this.decodeTga(buffer);
      } else {
        this.showFallback(fmt);
        return;
      }
    } catch {
      this.showFallback(fmt);
    } finally {
      this.loading.set(false);
    }
  }

  private fetchBlob(url: string): Promise<ArrayBuffer> {
    return new Promise<ArrayBuffer>((resolve, reject) => {
      this.http.get(url, { responseType: 'arraybuffer' }).subscribe({
        next: (buf) => resolve(buf),
        error: (err) => reject(err),
      });
    });
  }

  private async decodeTiff(buffer: ArrayBuffer): Promise<void> {
    const tiff = await import('tiff');
    const pages = tiff.decode(new Uint8Array(buffer));
    if (!pages.length) throw new Error('No pages in TIFF');
    const page = pages[0];
    const w = page.width;
    const h = page.height;
    const rgba = this.toRgba(page.data, w, h, page.components);
    this.renderToCanvas(rgba, w, h);
  }

  private async decodeExr(buffer: ArrayBuffer): Promise<void> {
    const { readExr, applyToneMapping } = await import('hdrify');
    const img = readExr(new Uint8Array(buffer));
    const ldr = applyToneMapping(img.data, img.width, img.height, {
      toneMapping: 'aces', exposure: 1.0,
    });
    this.renderLdrToCanvas(ldr, img.width, img.height);
  }

  private async decodeHdr(buffer: ArrayBuffer): Promise<void> {
    const { readHdr, applyToneMapping } = await import('hdrify');
    const img = readHdr(new Uint8Array(buffer));
    const ldr = applyToneMapping(img.data, img.width, img.height, {
      toneMapping: 'aces', exposure: 1.0,
    });
    this.renderLdrToCanvas(ldr, img.width, img.height);
  }

  private async decodeTga(buffer: ArrayBuffer): Promise<void> {
    const TgaModule = await import('tga-js');
    const TgaLoader = TgaModule.default;
    const tga = new TgaLoader();
    tga.load(new Uint8Array(buffer));
    const canvas = tga.getCanvas() as HTMLCanvasElement;
    this.copyCanvasToPreview(canvas);
  }

  /** Convert decoded TIFF data (variable components) to RGBA Uint8ClampedArray. */
  private toRgba(
    data: Uint8Array | Uint16Array | Float32Array | Float64Array,
    w: number, h: number, components: number,
  ): Uint8ClampedArray {
    const rgba = new Uint8ClampedArray(w * h * 4);
    const is16 = data instanceof Uint16Array;
    const isFloat = data instanceof Float32Array || data instanceof Float64Array;
    for (let i = 0; i < w * h; i++) {
      const si = i * components;
      const di = i * 4;
      let r = Number(data[si]);
      let g = components > 1 ? Number(data[si + 1]) : r;
      let b = components > 2 ? Number(data[si + 2]) : r;
      let a = components > 3 ? Number(data[si + 3]) : (is16 ? 65535 : (isFloat ? 1.0 : 255));
      if (is16) { r = r >> 8; g = g >> 8; b = b >> 8; a = a >> 8; }
      else if (isFloat) {
        r = Math.round(Math.min(1, Math.max(0, r)) * 255);
        g = Math.round(Math.min(1, Math.max(0, g)) * 255);
        b = Math.round(Math.min(1, Math.max(0, b)) * 255);
        a = Math.round(Math.min(1, Math.max(0, a)) * 255);
      }
      rgba[di] = r; rgba[di + 1] = g; rgba[di + 2] = b; rgba[di + 3] = a;
    }
    return rgba;
  }

  /** Render RGBA data to the preview canvas. */
  private renderToCanvas(rgba: Uint8ClampedArray, w: number, h: number): void {
    const el = this.previewCanvas()?.nativeElement;
    if (!el) return;
    el.width = w;
    el.height = h;
    const ctx = el.getContext('2d');
    if (!ctx) return;
    const imgData = ctx.createImageData(w, h);
    imgData.data.set(rgba);
    ctx.putImageData(imgData, 0, 0);
    this.canvasReady.set(true);
  }

  /** Render tone-mapped RGB (Uint8Array, 3 channels) from hdrify to the canvas. */
  private renderLdrToCanvas(ldr: Uint8Array, w: number, h: number): void {
    const rgba = new Uint8ClampedArray(w * h * 4);
    for (let i = 0; i < w * h; i++) {
      rgba[i * 4] = ldr[i * 3];
      rgba[i * 4 + 1] = ldr[i * 3 + 1];
      rgba[i * 4 + 2] = ldr[i * 3 + 2];
      rgba[i * 4 + 3] = 255;
    }
    this.renderToCanvas(rgba, w, h);
  }

  /** Copy a decoded canvas (e.g. from tga-js) onto our preview canvas. */
  private copyCanvasToPreview(source: HTMLCanvasElement): void {
    const el = this.previewCanvas()?.nativeElement;
    if (!el) return;
    el.width = source.width;
    el.height = source.height;
    const ctx = el.getContext('2d');
    if (!ctx) return;
    ctx.drawImage(source, 0, 0);
    this.canvasReady.set(true);
  }

  private showFallback(fmt: string): void {
    const label = OUTPUT_FORMATS.find(f => f.value === fmt)?.label ?? fmt;
    this.errorMessage.set(`Could not decode ${label}. Showing thumbnail preview.`);
  }
}
