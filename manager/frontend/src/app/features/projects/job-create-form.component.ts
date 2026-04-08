// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogRef, MatDialogModule } from '@angular/material/dialog';
import { FormsModule, ReactiveFormsModule, FormGroup, FormControl, Validators,
  AbstractControl, ValidationErrors } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatRadioModule } from '@angular/material/radio';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { JobService } from '../../core/services/job.service';
import { TiledJobService } from '../../core/services/tiled-job.service';
import { AnimationService } from '../../core/services/animation.service';
import { SystemInfoService } from '../../core/services/system-info.service';
import { RENDER_ENGINES, RENDER_DEVICES, TILING_OPTIONS, ANIMATION_TILING_OPTIONS,
  OUTPUT_FORMATS, TILED_OUTPUT_FORMATS, HDR_FORMATS } from './render-payload.util';
import { buildSingleJobPayload, buildTiledJobPayload, buildAnimationPayload } from './job-create-payload.util';
import { RenderType, JobCreateDialogData } from './job-create-form.types';
import { VideoOutputSectionComponent } from './video-output-section.component';
import { ResolutionInputComponent } from './resolution-input.component';

@Component({
  selector: 'app-job-create-form',
  standalone: true,
  imports: [
    FormsModule, ReactiveFormsModule, MatDialogModule, MatFormFieldModule,
    MatInputModule, MatSelectModule, MatRadioModule,
    MatButtonModule, MatIconModule, MatSnackBarModule, VideoOutputSectionComponent, ResolutionInputComponent,
  ],
  template: `
    <h2 mat-dialog-title>Create Job</h2>
    <mat-dialog-content>
      <mat-radio-group [(ngModel)]="renderType" (ngModelChange)="resetFormatIfNeeded()"
                       class="type-selector">
        <mat-radio-button value="single"><mat-icon>image</mat-icon> Single</mat-radio-button>
        <mat-radio-button value="tiled"><mat-icon>grid_view</mat-icon> Tiled</mat-radio-button>
        <mat-radio-button value="animation"><mat-icon>movie</mat-icon> Animation</mat-radio-button>
      </mat-radio-group>
      <form [formGroup]="form" class="render-form">
        <div class="form-row">
          <mat-form-field><mat-label>Name</mat-label>
            <input matInput formControlName="name" /></mat-form-field>
          <mat-form-field><mat-label>Engine</mat-label>
            <mat-select formControlName="renderEngine">
              @for (e of engines; track e.value) {
                <mat-option [value]="e.value">{{ e.label }}</mat-option>
              }</mat-select></mat-form-field>
          <mat-form-field><mat-label>Device</mat-label>
            <mat-select formControlName="renderDevice">
              @for (d of devices; track d.value) {
                <mat-option [value]="d.value">{{ d.label }}</mat-option>
              }</mat-select></mat-form-field>
        </div>
        <div class="form-row">
          <mat-form-field><mat-label>Samples</mat-label>
            <input matInput type="number" formControlName="samples" /></mat-form-field>
        </div>
        <app-resolution-input [parentForm]="form"></app-resolution-input>
        <div class="form-row">
          @if (renderType === 'single') {
            <mat-form-field><mat-label>Frame</mat-label>
              <input matInput type="number" formControlName="frame" /></mat-form-field>
          }
          @if (renderType === 'tiled') {
            <mat-form-field><mat-label>Tiling</mat-label>
              <mat-select formControlName="tilingConfig">
                @for (t of tilingOptions; track t.value) {
                  <mat-option [value]="t.value">{{ t.label }}</mat-option>
                }</mat-select></mat-form-field>
          }
          @if (renderType === 'animation') {
            <mat-form-field><mat-label>Start Frame</mat-label>
              <input matInput type="number" formControlName="startFrame" /></mat-form-field>
            <mat-form-field><mat-label>End Frame</mat-label>
              <input matInput type="number" formControlName="endFrame" />
              @if (form.hasError('endFrameBeforeStart')) {
                <mat-error>End frame must be >= start frame</mat-error>
              }</mat-form-field>
            <mat-form-field><mat-label>Frame Step</mat-label>
              <input matInput type="number" formControlName="frameStep" /></mat-form-field>
            <mat-form-field><mat-label>Tiling</mat-label>
              <mat-select formControlName="animTilingConfig"
                          (selectionChange)="resetFormatIfNeeded()">
                @for (t of animTilingOptions; track t.value) {
                  <mat-option [value]="t.value">{{ t.label }}</mat-option>
                }</mat-select></mat-form-field>
          }
          <mat-form-field><mat-label>Output Format</mat-label>
            <mat-select formControlName="outputFormat">
              @for (f of availableFormats; track f.value) {
                <mat-option [value]="f.value">{{ f.label }}</mat-option>
              }</mat-select></mat-form-field>
        </div>
        @if (form.value.outputFormat === 'JPEG') {
          <div class="form-row"><mat-form-field><mat-label>JPEG Quality (1-100)</mat-label>
            <input matInput type="number" formControlName="jpegQuality" /></mat-form-field></div>
        }
        @if (form.value.outputFormat === 'OPEN_EXR' || form.value.outputFormat === 'OPEN_EXR_MULTILAYER') {
          <div class="form-row"><mat-form-field><mat-label>Color Depth</mat-label>
            <mat-select formControlName="colorDepth">
              <mat-option value="16">Half Float (16-bit)</mat-option>
              <mat-option value="32">Full Float (32-bit)</mat-option>
            </mat-select></mat-form-field></div>
        }
        @if (ffmpegAvailable && renderType === 'animation') {
          <app-video-output-section [parentForm]="form"
            [outputFormat]="form.value.outputFormat ?? ''" />
        }
      </form>
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button mat-dialog-close>Cancel</button>
      <button mat-flat-button color="primary"
              [disabled]="form.invalid || submitting" (click)="onSubmit()">
        @switch (renderType) {
          @case ('single') { Create Job }
          @case ('tiled') { Create Tiled Job }
          @case ('animation') { Create Animation }
        }
      </button>
    </mat-dialog-actions>
  `,
  styles: [`
    .type-selector { display: flex; gap: 24px; margin-bottom: 16px; }
    .type-selector mat-icon { vertical-align: middle; margin-right: 4px; }
    .render-form { display: flex; flex-direction: column; gap: 8px; }
    .form-row { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }
    .form-row mat-form-field { flex: 1; min-width: 120px; }
  `],
})
export class JobCreateFormComponent {
  private readonly dialogRef = inject(MatDialogRef<JobCreateFormComponent>);
  private readonly data: JobCreateDialogData = inject(MAT_DIALOG_DATA);
  private readonly jobService = inject(JobService);
  private readonly tiledJobService = inject(TiledJobService);
  private readonly animationService = inject(AnimationService);
  private readonly snackBar = inject(MatSnackBar);
  private readonly systemInfoService = inject(SystemInfoService);

  engines = RENDER_ENGINES;
  devices = RENDER_DEVICES;
  tilingOptions = TILING_OPTIONS;
  animTilingOptions = ANIMATION_TILING_OPTIONS;
  renderType: RenderType = 'single';
  submitting = false;
  ffmpegAvailable = false;

  get availableFormats(): typeof OUTPUT_FORMATS {
    if (this.renderType === 'tiled') return TILED_OUTPUT_FORMATS;
    if (this.renderType === 'animation' && this.form?.value.animTilingConfig !== 'NONE') {
      return TILED_OUTPUT_FORMATS;
    }
    return OUTPUT_FORMATS;
  }

  resetFormatIfNeeded(): void {
    const current = this.form.value.outputFormat;
    if (current && !this.availableFormats.some(f => f.value === current)) {
      this.form.patchValue({ outputFormat: 'PNG' });
    }
    if (current && HDR_FORMATS.has(current)) {
      this.form.patchValue({ generateVideo: false });
    }
  }

  form = new FormGroup({
    name: new FormControl('', [
      Validators.required, Validators.minLength(4), Validators.maxLength(40),
    ]),
    renderEngine: new FormControl('CYCLES', Validators.required),
    renderDevice: new FormControl('ANY', Validators.required),
    samples: new FormControl(128, [Validators.required, Validators.min(1)]),
    resolutionX: new FormControl(1920, [Validators.required, Validators.min(1)]),
    resolutionY: new FormControl(1080, [Validators.required, Validators.min(1)]),
    resolutionMode: new FormControl<'preset' | 'custom' | null>('preset', Validators.required),
    resolutionPreset: new FormControl<string | null>('fhd1080', Validators.required),
    outputFormat: new FormControl('PNG', Validators.required),
    jpegQuality: new FormControl(90, [Validators.required, Validators.min(1), Validators.max(100)]),
    colorDepth: new FormControl('16', Validators.required),
    frame: new FormControl(1, [Validators.required, Validators.min(1)]),
    tilingConfig: new FormControl('4x4', Validators.required),
    startFrame: new FormControl(1, [Validators.required, Validators.min(1)]),
    endFrame: new FormControl(250, [Validators.required, Validators.min(1)]),
    frameStep: new FormControl(1, [Validators.required, Validators.min(1)]),
    animTilingConfig: new FormControl('NONE', Validators.required),
    generateVideo: new FormControl(false),
    videoPreset: new FormControl('web_h264'),
    videoFramerate: new FormControl(24, [Validators.min(1), Validators.max(120)]),
    videoContainer: new FormControl('mp4'),
    videoCodec: new FormControl('libx264'),
    videoCrf: new FormControl(23, [Validators.min(0), Validators.max(51)]),
  }, { validators: [JobCreateFormComponent.frameRangeValidator] });

  constructor() {
    this.systemInfoService.getSystemInfo().subscribe(info => {
      this.ffmpegAvailable = info.ffmpeg_available;
    });
    if (!this.data.prefill) return;
    const p = this.data.prefill;
    this.renderType = p.renderType;
    this.form.patchValue({
      renderEngine: p.renderEngine ?? 'CYCLES', renderDevice: p.renderDevice ?? 'ANY',
      samples: p.samples ?? 128, resolutionX: p.resolutionX ?? 1920,
      resolutionY: p.resolutionY ?? 1080, frame: p.frame ?? 1,
      tilingConfig: p.tilingConfig ?? '4x4', startFrame: p.startFrame ?? 1,
      endFrame: p.endFrame ?? 250, frameStep: p.frameStep ?? 1,
      animTilingConfig: p.animTilingConfig ?? 'NONE',
    });
  }

  private static frameRangeValidator(g: AbstractControl): ValidationErrors | null {
    const s = g.get('startFrame')?.value, e = g.get('endFrame')?.value;
    return s != null && e != null && e < s ? { endFrameBeforeStart: true } : null;
  }

  onSubmit(): void {
    if (this.form.invalid) return;
    this.submitting = true;
    const v = this.form.getRawValue();
    const h = { next: (r: { name: string }) => this.done(r.name), error: (e: { error?: Record<string, unknown> }) => this.fail(e) };
    if (this.renderType === 'single') {
      this.jobService.create(buildSingleJobPayload(v, this.data.assetId)).subscribe(h);
    } else if (this.renderType === 'tiled') {
      this.tiledJobService.create(buildTiledJobPayload(v, this.data.projectId, this.data.assetId)).subscribe(h);
    } else {
      this.animationService.create(buildAnimationPayload(v, this.data.projectId, this.data.assetId)).subscribe(h);
    }
  }

  private done(name: string): void {
    this.submitting = false;
    this.snackBar.open(`Created "${name}"`, 'Dismiss', { duration: 3000 });
    this.dialogRef.close(name);
  }

  private fail(err: { error?: Record<string, unknown> }): void {
    this.submitting = false;
    const b = err.error;
    const msg = (b?.['name'] as string[])?.[0] || (b?.['detail'] as string)
      || (b?.['non_field_errors'] as string[])?.[0] || 'Failed to create job';
    this.snackBar.open(msg, 'Dismiss', { duration: 5000 });
  }
}
