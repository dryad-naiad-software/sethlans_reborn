// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Component, viewChild } from '@angular/core';
import { FormGroup, FormControl } from '@angular/forms';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { By } from '@angular/platform-browser';
import { VideoOutputSectionComponent } from './video-output-section.component';

/**
 * Spec FR §106 / AC §473: the `generateVideo` checkbox must render disabled
 * while `videoAssemblyReady === false` (or for HDR formats). Reactive forms'
 * `setDisabledState()` overrides any template `[disabled]` Input, so the
 * component drives this via an `effect()` that calls
 * `generateVideoCtrl.disable()/enable()` on the FormControl itself.
 *
 * These tests assert the resulting DOM contract: the underlying `<input>`
 * carries `disabled === true` for the not-ready and HDR cases, and
 * `disabled === false` for the ready + non-HDR case. They also guard against
 * the Angular reactive-forms warning about combining `[disabled]` with
 * `[formControl]` regressing.
 */

/**
 * Test host that lets each spec set the `assemblyReady` and `assemblyLoading`
 * inputs declaratively. The form mirrors the slice of `JobCreateFormComponent`
 * that this child component reads (`generateVideo`, `videoPreset`,
 * `videoFramerate`, `videoContainer`, `videoCodec`, `videoCrf`).
 */
@Component({
  standalone: true,
  imports: [VideoOutputSectionComponent],
  template: `
    <app-video-output-section
      [parentForm]="form"
      [outputFormat]="outputFormat"
      [assemblyReady]="assemblyReady"
      [assemblyLoading]="assemblyLoading"
    />
  `,
})
class HostComponent {
  outputFormat = 'PNG';
  assemblyReady = true;
  assemblyLoading = false;
  form = new FormGroup({
    generateVideo: new FormControl(false),
    videoPreset: new FormControl('web_h264'),
    videoFramerate: new FormControl(24),
    videoContainer: new FormControl('mp4'),
    videoCodec: new FormControl('libx264'),
    videoCrf: new FormControl(23),
  });
  readonly section = viewChild(VideoOutputSectionComponent);
}

function checkboxInput(fixture: ComponentFixture<HostComponent>): HTMLInputElement {
  // mat-checkbox renders a real <input type="checkbox"> internally; the
  // FormControl's disabled state is reflected onto its `disabled` property.
  const el = fixture.debugElement.query(By.css('mat-checkbox input'));
  expect(el).withContext('expected <mat-checkbox input> in DOM').not.toBeNull();
  return el.nativeElement as HTMLInputElement;
}

describe('VideoOutputSectionComponent', () => {
  let fixture: ComponentFixture<HostComponent>;
  let host: HostComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HostComponent, NoopAnimationsModule],
    }).compileComponents();

    fixture = TestBed.createComponent(HostComponent);
    host = fixture.componentInstance;
  });

  describe('assemblyReady === true (non-HDR format)', () => {
    beforeEach(() => {
      host.assemblyReady = true;
      host.assemblyLoading = false;
      fixture.detectChanges();
    });

    it('reflects assemblyReady=true into the section component instance', () => {
      const section = host.section();
      expect(section).toBeTruthy();
      expect(section!.assemblyReady()).toBeTrue();
    });

    it('does NOT render the assembly-hint block', () => {
      expect(fixture.debugElement.query(By.css('.assembly-hint'))).toBeNull();
    });

    it('does NOT render the assembly-progress spinner', () => {
      const spinner = fixture.debugElement.query(
        By.css('.assembly-hint mat-progress-spinner'),
      );
      expect(spinner).toBeNull();
    });

    it('renders the generateVideo checkbox enabled (input.disabled === false)', () => {
      expect(checkboxInput(fixture).disabled).toBeFalse();
      expect(host.form.controls.generateVideo.disabled).toBeFalse();
    });
  });

  describe('assemblyReady === false (FFmpeg not yet ready)', () => {
    beforeEach(() => {
      host.assemblyReady = false;
      host.assemblyLoading = false;
      fixture.detectChanges();
    });

    it('renders the assembly hint block with the prepare-message', () => {
      const hint = fixture.debugElement.query(By.css('.assembly-hint'));
      expect(hint).not.toBeNull();
      expect(hint.nativeElement.textContent).toContain(
        'Video assembly is preparing',
      );
      expect(hint.nativeElement.textContent).toContain('refresh in a moment');
    });

    it('reflects assemblyReady=false into the section component instance', () => {
      const section = host.section();
      expect(section).toBeTruthy();
      expect(section!.assemblyReady()).toBeFalse();
    });

    it('does NOT render the in-flight spinner when assemblyLoading is false', () => {
      const spinner = fixture.debugElement.query(
        By.css('.assembly-hint mat-progress-spinner'),
      );
      expect(spinner).toBeNull();
    });

    it('renders the generateVideo checkbox disabled (input.disabled === true)', () => {
      expect(checkboxInput(fixture).disabled).toBeTrue();
      expect(host.form.controls.generateVideo.disabled).toBeTrue();
    });

    it('re-enables the checkbox once assemblyReady flips to true', () => {
      host.assemblyReady = true;
      fixture.detectChanges();
      expect(checkboxInput(fixture).disabled).toBeFalse();
      expect(host.form.controls.generateVideo.disabled).toBeFalse();
    });
  });

  describe('assemblyReady === false AND assemblyLoading === true', () => {
    beforeEach(() => {
      host.assemblyReady = false;
      host.assemblyLoading = true;
      fixture.detectChanges();
    });

    it('renders the in-flight progress spinner', () => {
      const spinner = fixture.debugElement.query(
        By.css('.assembly-hint mat-progress-spinner'),
      );
      expect(spinner).not.toBeNull();
    });

    it('keeps assemblyReady=false on the section component instance', () => {
      const section = host.section();
      expect(section!.assemblyReady()).toBeFalse();
    });

    it('keeps the checkbox disabled while loading', () => {
      expect(checkboxInput(fixture).disabled).toBeTrue();
    });
  });

  describe('HDR formats — disabled regardless of assemblyReady', () => {
    it('reports isHdrFormat=true on the section instance for an HDR format', () => {
      host.assemblyReady = true;
      host.outputFormat = 'OPEN_EXR';
      fixture.detectChanges();
      const section = host.section();
      expect(section!.isHdrFormat()).toBeTrue();
    });

    it('renders the HDR hint paragraph for an HDR format', () => {
      host.assemblyReady = true;
      host.outputFormat = 'OPEN_EXR';
      fixture.detectChanges();
      const hdr = fixture.debugElement.query(By.css('.hdr-hint'));
      expect(hdr).not.toBeNull();
      expect(hdr.nativeElement.textContent).toContain(
        'not available for HDR formats',
      );
    });

    it('disables the checkbox for an HDR format even when assemblyReady=true', () => {
      host.assemblyReady = true;
      host.outputFormat = 'OPEN_EXR';
      fixture.detectChanges();
      expect(checkboxInput(fixture).disabled).toBeTrue();
      expect(host.form.controls.generateVideo.disabled).toBeTrue();
    });

    it('re-enables the checkbox when leaving an HDR format', () => {
      host.assemblyReady = true;
      host.outputFormat = 'OPEN_EXR';
      fixture.detectChanges();
      expect(checkboxInput(fixture).disabled).toBeTrue();

      host.outputFormat = 'PNG';
      fixture.detectChanges();
      expect(checkboxInput(fixture).disabled).toBeFalse();
    });
  });

  describe('reactive-forms warning regression guard', () => {
    it('does NOT emit the "[disabled] + reactive form directive" warning', () => {
      const warnSpy = spyOn(console, 'warn').and.callThrough();
      const errorSpy = spyOn(console, 'error').and.callThrough();

      host.assemblyReady = false;
      fixture.detectChanges();
      host.assemblyReady = true;
      fixture.detectChanges();
      host.outputFormat = 'OPEN_EXR';
      fixture.detectChanges();

      const offending = (msg: unknown) =>
        typeof msg === 'string' &&
        msg.includes('disabled attribute') &&
        msg.includes('reactive form');

      const warnedAboutDisabled = warnSpy.calls.allArgs().some(
        args => args.some(offending),
      );
      const erroredAboutDisabled = errorSpy.calls.allArgs().some(
        args => args.some(offending),
      );
      expect(warnedAboutDisabled).toBeFalse();
      expect(erroredAboutDisabled).toBeFalse();
    });
  });
});
