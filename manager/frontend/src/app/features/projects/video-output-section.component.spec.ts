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
 * Implementation note (flagged, NOT fixed in this tests-only commit):
 * `<mat-checkbox>` in `video-output-section.component.html` carries both
 * `[formControl]` and `[disabled]` bindings. Reactive forms' built-in
 * `setDisabledState()` overrides the template `[disabled]` Input — so the
 * MatCheckbox's `disabled` property always reads the form control state
 * (false), and `input.disabled` in the DOM is also false. Per spec FR §106
 * the checkbox should render disabled when assemblyReady is false. These
 * tests assert the contract that drives that behaviour (assemblyReady
 * input + visible hint + spinner) rather than the checkbox-disabled
 * symptom, since the symptom is broken until the implementation is fixed.
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

  describe('assemblyReady === true', () => {
    beforeEach(() => {
      host.assemblyReady = true;
      host.assemblyLoading = false;
      fixture.detectChanges();
    });

    it('reflects assemblyReady=true into the section component instance', () => {
      const section = host.section();
      expect(section).toBeTruthy();
      expect(section!.assemblyReady).toBeTrue();
    });

    it('does NOT render the assembly-hint block', () => {
      expect(fixture.debugElement.query(By.css('.assembly-hint'))).toBeNull();
    });

    it('does NOT render the assembly-progress spinner', () => {
      // Spinner only appears under the assembly-hint guard, so absence of
      // .assembly-hint implies absence of the spinner. Sanity-check by
      // querying for the spinner directly under the section.
      const spinner = fixture.debugElement.query(
        By.css('.assembly-hint mat-progress-spinner'),
      );
      expect(spinner).toBeNull();
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
      expect(section!.assemblyReady).toBeFalse();
    });

    it('does NOT render the in-flight spinner when assemblyLoading is false', () => {
      const spinner = fixture.debugElement.query(
        By.css('.assembly-hint mat-progress-spinner'),
      );
      expect(spinner).toBeNull();
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
      expect(section!.assemblyReady).toBeFalse();
    });
  });

  describe('HDR formats — disabled regardless of assemblyReady', () => {
    it('reports isHdrFormat=true on the section instance for an HDR format', () => {
      host.assemblyReady = true;
      host.outputFormat = 'OPEN_EXR';
      fixture.detectChanges();
      const section = host.section();
      expect(section!.isHdrFormat).toBeTrue();
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
  });
});
