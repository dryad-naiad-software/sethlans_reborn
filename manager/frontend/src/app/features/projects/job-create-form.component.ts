// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogRef, MatDialogModule } from '@angular/material/dialog';
import { FormsModule } from '@angular/forms';
import {
  ReactiveFormsModule, FormGroup, FormControl, Validators,
  AbstractControl, ValidationErrors,
} from '@angular/forms';
import { MatCardModule } from '@angular/material/card';
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
import {
  generateOutputFilePattern, buildRenderSettings, buildTiledRenderSettings,
  parseTilingConfig, RENDER_ENGINES, RENDER_DEVICES, TILING_OPTIONS,
  ANIMATION_TILING_OPTIONS, OUTPUT_FORMATS,
} from './render-payload.util';
import { RenderType, JobCreateDialogData } from './job-create-form.types';

@Component({
  selector: 'app-job-create-form',
  standalone: true,
  imports: [
    FormsModule, ReactiveFormsModule, MatDialogModule, MatFormFieldModule,
    MatInputModule, MatSelectModule, MatRadioModule,
    MatButtonModule, MatIconModule, MatSnackBarModule,
  ],
  template: `
    <h2 mat-dialog-title>Create Job</h2>
    <mat-dialog-content>
      <mat-radio-group [(ngModel)]="renderType" class="type-selector">
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
          <mat-form-field><mat-label>Resolution X</mat-label>
            <input matInput type="number" formControlName="resolutionX" /></mat-form-field>
          <span class="res-x">x</span>
          <mat-form-field><mat-label>Resolution Y</mat-label>
            <input matInput type="number" formControlName="resolutionY" /></mat-form-field>
        </div>
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
              <mat-select formControlName="animTilingConfig">
                @for (t of animTilingOptions; track t.value) {
                  <mat-option [value]="t.value">{{ t.label }}</mat-option>
                }</mat-select></mat-form-field>
          }
          <mat-form-field><mat-label>Output Format</mat-label>
            <mat-select formControlName="outputFormat">
              @for (f of outputFormats; track f.value) {
                <mat-option [value]="f.value">{{ f.label }}</mat-option>
              }</mat-select></mat-form-field>
        </div>
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
    .res-x { padding-top: 12px; }
  `],
})
export class JobCreateFormComponent {
  private readonly dialogRef = inject(MatDialogRef<JobCreateFormComponent>);
  private readonly data: JobCreateDialogData = inject(MAT_DIALOG_DATA);
  private readonly jobService = inject(JobService);
  private readonly tiledJobService = inject(TiledJobService);
  private readonly animationService = inject(AnimationService);
  private readonly snackBar = inject(MatSnackBar);

  engines = RENDER_ENGINES;
  devices = RENDER_DEVICES;
  tilingOptions = TILING_OPTIONS;
  animTilingOptions = ANIMATION_TILING_OPTIONS;
  outputFormats = OUTPUT_FORMATS;
  renderType: RenderType = 'single';
  submitting = false;

  form = new FormGroup({
    name: new FormControl('', [
      Validators.required, Validators.minLength(4), Validators.maxLength(40),
    ]),
    renderEngine: new FormControl('CYCLES', Validators.required),
    renderDevice: new FormControl('ANY', Validators.required),
    samples: new FormControl(128, [Validators.required, Validators.min(1)]),
    resolutionX: new FormControl(1920, [Validators.required, Validators.min(1)]),
    resolutionY: new FormControl(1080, [Validators.required, Validators.min(1)]),
    outputFormat: new FormControl('PNG', Validators.required),
    frame: new FormControl(1, [Validators.required, Validators.min(1)]),
    tilingConfig: new FormControl('4x4', Validators.required),
    startFrame: new FormControl(1, [Validators.required, Validators.min(1)]),
    endFrame: new FormControl(250, [Validators.required, Validators.min(1)]),
    frameStep: new FormControl(1, [Validators.required, Validators.min(1)]),
    animTilingConfig: new FormControl('NONE', Validators.required),
  }, { validators: [JobCreateFormComponent.frameRangeValidator] });

  constructor() {
    if (this.data.prefill) {
      const p = this.data.prefill;
      this.renderType = p.renderType;
      this.form.patchValue({
        renderEngine: p.renderEngine ?? 'CYCLES',
        renderDevice: p.renderDevice ?? 'ANY',
        samples: p.samples ?? 128,
        resolutionX: p.resolutionX ?? 1920,
        resolutionY: p.resolutionY ?? 1080,
        frame: p.frame ?? 1,
        tilingConfig: p.tilingConfig ?? '4x4',
        startFrame: p.startFrame ?? 1,
        endFrame: p.endFrame ?? 250,
        frameStep: p.frameStep ?? 1,
        animTilingConfig: p.animTilingConfig ?? 'NONE',
      });
    }
  }

  private static frameRangeValidator(group: AbstractControl): ValidationErrors | null {
    const start = group.get('startFrame')?.value;
    const end = group.get('endFrame')?.value;
    if (start != null && end != null && end < start) {
      return { endFrameBeforeStart: true };
    }
    return null;
  }

  onSubmit(): void {
    if (this.form.invalid) return;
    this.submitting = true;
    const v = this.form.getRawValue();
    switch (this.renderType) {
      case 'single': this.createSingle(v); break;
      case 'tiled': this.createTiled(v); break;
      case 'animation': this.createAnim(v); break;
    }
  }

  private createSingle(v: ReturnType<typeof this.form.getRawValue>): void {
    this.jobService.create({
      name: v.name!, asset_id: this.data.assetId,
      output_file_pattern: generateOutputFilePattern(v.name!),
      start_frame: v.frame!, end_frame: v.frame!,
      render_engine: v.renderEngine!, render_device: v.renderDevice!,
      render_settings: buildRenderSettings(v.samples!, v.resolutionX!, v.resolutionY!),
    }).subscribe({ next: (j) => this.done(j.name), error: (e) => this.fail(e) });
  }

  private createTiled(v: ReturnType<typeof this.form.getRawValue>): void {
    const t = parseTilingConfig(v.tilingConfig!);
    this.tiledJobService.create({
      name: v.name!, project: this.data.projectId, asset_id: this.data.assetId,
      final_resolution_x: v.resolutionX!, final_resolution_y: v.resolutionY!,
      tile_count_x: t.tile_count_x, tile_count_y: t.tile_count_y,
      render_engine: v.renderEngine!, render_device: v.renderDevice!,
      render_settings: buildTiledRenderSettings(v.samples!),
    }).subscribe({ next: (j) => this.done(j.name), error: (e) => this.fail(e) });
  }

  private createAnim(v: ReturnType<typeof this.form.getRawValue>): void {
    this.animationService.create({
      name: v.name!, project: this.data.projectId, asset_id: this.data.assetId,
      output_file_pattern: generateOutputFilePattern(v.name!),
      start_frame: v.startFrame!, end_frame: v.endFrame!, frame_step: v.frameStep!,
      tiling_config: v.animTilingConfig!,
      render_engine: v.renderEngine!, render_device: v.renderDevice!,
      render_settings: buildRenderSettings(v.samples!, v.resolutionX!, v.resolutionY!),
    }).subscribe({ next: (a) => this.done(a.name), error: (e) => this.fail(e) });
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
