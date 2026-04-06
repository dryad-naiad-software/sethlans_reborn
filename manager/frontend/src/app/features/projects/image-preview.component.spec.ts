// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { HttpClient } from '@angular/common/http';
import { Component, viewChild } from '@angular/core';
import { ImagePreviewComponent } from './image-preview.component';

/**
 * Test host that wraps ImagePreviewComponent so we can set inputs
 * declaratively and trigger change detection.
 */
@Component({
  standalone: true,
  imports: [ImagePreviewComponent],
  template: `
    <app-image-preview
      [src]="src"
      [format]="format"
      [alt]="alt"
      [thumbnailSrc]="thumbnailSrc"
    />
  `,
})
class TestHostComponent {
  src: string | null = null;
  format = 'PNG';
  alt = 'test image';
  thumbnailSrc: string | null = null;
  readonly preview = viewChild(ImagePreviewComponent);
}

/**
 * Build a 2x2 RGBA pixel array simulating decoded TIFF page data.
 * 4 pixels, 4 components each = 16 bytes. Red/Green/Blue/White pixels.
 */
function makeTiffRgba(): Uint8ClampedArray {
  return new Uint8ClampedArray([
    255, 0, 0, 255,      // red pixel
    0, 255, 0, 255,      // green pixel
    0, 0, 255, 255,      // blue pixel
    255, 255, 255, 255,  // white pixel
  ]);
}

describe('ImagePreviewComponent', () => {
  let fixture: ComponentFixture<TestHostComponent>;
  let host: TestHostComponent;
  let httpSpy: jasmine.SpyObj<HttpClient>;

  beforeEach(async () => {
    httpSpy = jasmine.createSpyObj('HttpClient', ['get']);

    await TestBed.configureTestingModule({
      imports: [TestHostComponent],
      providers: [
        { provide: HttpClient, useValue: httpSpy },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(TestHostComponent);
    host = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create the component', () => {
    expect(host.preview()).toBeTruthy();
  });

  it('should always have the canvas element in the DOM', () => {
    const canvas = fixture.nativeElement.querySelector('canvas');
    expect(canvas).toBeTruthy('canvas must exist in DOM unconditionally');
    expect(canvas.classList.contains('hidden')).toBeTrue();
  });

  it('should hide canvas when canvasReady is false', () => {
    const canvas: HTMLCanvasElement = fixture.nativeElement.querySelector('canvas');
    expect(canvas.classList.contains('hidden')).toBeTrue();
  });

  it('should show canvas (remove hidden class) when canvasReady is true', () => {
    const comp = host.preview()!;
    comp.canvasReady.set(true);
    fixture.detectChanges();

    const canvas: HTMLCanvasElement = fixture.nativeElement.querySelector('canvas');
    expect(canvas.classList.contains('hidden')).toBeFalse();
  });

  it('should render native img for PNG format', () => {
    host.src = '/media/output/test.png';
    host.format = 'PNG';
    fixture.detectChanges();

    const img: HTMLImageElement = fixture.nativeElement.querySelector('img');
    expect(img).toBeTruthy();
    expect(img.src).toContain('/media/output/test.png');
  });

  it('should show spinner during loading', () => {
    const comp = host.preview()!;
    comp.loading.set(true);
    fixture.detectChanges();

    const spinner = fixture.nativeElement.querySelector('mat-spinner');
    expect(spinner).toBeTruthy();
  });

  it('should display error message with fallback thumbnail', () => {
    const comp = host.preview()!;
    host.thumbnailSrc = '/media/thumbs/test.png';
    comp.errorMessage.set('Could not decode TIFF. Showing thumbnail preview.');
    fixture.detectChanges();

    const errorText: HTMLElement =
      fixture.nativeElement.querySelector('.decode-error');
    expect(errorText).toBeTruthy();
    expect(errorText.textContent).toContain('Could not decode TIFF');
  });

  it('canvas viewChild should resolve even during loading state', () => {
    const comp = host.preview()!;
    comp.loading.set(true);
    fixture.detectChanges();

    // This is the core fix validation: canvas is always in DOM
    expect(comp.previewCanvas()).toBeTruthy();
    expect(comp.previewCanvas()!.nativeElement)
      .toBeInstanceOf(HTMLCanvasElement);
  });

  it('should render TIFF RGBA data to canvas and set canvasReady', () => {
    const comp = host.preview()!;
    fixture.detectChanges();

    const canvas: HTMLCanvasElement =
      fixture.nativeElement.querySelector('canvas');

    // Verify canvas starts with default dimensions
    expect(canvas.width).toBe(300);
    expect(comp.canvasReady()).toBeFalse();

    // Simulate what decodeTiff does: render RGBA data to canvas
    const rgba = makeTiffRgba();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (comp as any)['renderToCanvas'](rgba, 2, 2);

    // Canvas should now have dimensions set and canvasReady should be true
    expect(canvas.width).toBe(2);
    expect(canvas.height).toBe(2);
    expect(comp.canvasReady()).toBeTrue();

    // Verify actual pixel data was written
    const ctx = canvas.getContext('2d')!;
    const imgData = ctx.getImageData(0, 0, 2, 2);
    // First pixel: red (255, 0, 0, 255)
    expect(imgData.data[0]).toBe(255);
    expect(imgData.data[1]).toBe(0);
    expect(imgData.data[2]).toBe(0);
    expect(imgData.data[3]).toBe(255);
    // Second pixel: green (0, 255, 0, 255)
    expect(imgData.data[4]).toBe(0);
    expect(imgData.data[5]).toBe(255);
    expect(imgData.data[6]).toBe(0);
    expect(imgData.data[7]).toBe(255);
  });

  it('should write TIFF pixel data to canvas while loading and persist after', () => {
    const comp = host.preview()!;
    comp.loading.set(true);
    fixture.detectChanges();

    // Canvas should exist even while loading (core bug fix)
    const canvas = comp.previewCanvas()!.nativeElement;
    expect(canvas).toBeTruthy();

    // Write to canvas while loading is true (simulates decode completing)
    const rgba = new Uint8ClampedArray([128, 64, 32, 255]);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (comp as any)['renderToCanvas'](rgba, 1, 1);
    expect(comp.canvasReady()).toBeTrue();

    // Now set loading to false (simulates the finally block)
    comp.loading.set(false);
    fixture.detectChanges();

    // Verify canvas still has the data and is visible
    const visibleCanvas: HTMLCanvasElement =
      fixture.nativeElement.querySelector('canvas:not(.hidden)');
    expect(visibleCanvas).toBeTruthy('canvas should be visible after loading');
    const ctx = visibleCanvas.getContext('2d')!;
    const pixel = ctx.getImageData(0, 0, 1, 1);
    expect(pixel.data[0]).toBe(128);
    expect(pixel.data[1]).toBe(64);
    expect(pixel.data[2]).toBe(32);
    expect(pixel.data[3]).toBe(255);
  });

  it('should copy source canvas to preview canvas via copyCanvasToPreview', () => {
    const comp = host.preview()!;
    fixture.detectChanges();

    // Create a source canvas simulating tga-js output
    const source = document.createElement('canvas');
    source.width = 3;
    source.height = 3;
    const srcCtx = source.getContext('2d')!;
    srcCtx.fillStyle = '#ff0000';
    srcCtx.fillRect(0, 0, 3, 3);

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (comp as any)['copyCanvasToPreview'](source);

    const preview = comp.previewCanvas()!.nativeElement;
    expect(preview.width).toBe(3);
    expect(preview.height).toBe(3);
    expect(comp.canvasReady()).toBeTrue();

    const ctx = preview.getContext('2d')!;
    const pixel = ctx.getImageData(0, 0, 1, 1);
    expect(pixel.data[0]).toBe(255); // red
    expect(pixel.data[1]).toBe(0);
    expect(pixel.data[2]).toBe(0);
    expect(pixel.data[3]).toBe(255);
  });
});
