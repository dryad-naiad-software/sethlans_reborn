// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, Input, OnInit, OnDestroy } from '@angular/core';
import { ReactiveFormsModule, FormGroup, FormControl } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatIconModule } from '@angular/material/icon';
import { Subscription } from 'rxjs';
import { VIDEO_PRESETS, HDR_FORMATS } from './render-payload.util';

@Component({
  selector: 'app-video-output-section',
  standalone: true,
  imports: [
    ReactiveFormsModule, MatFormFieldModule, MatInputModule,
    MatSelectModule, MatCheckboxModule, MatExpansionModule, MatIconModule,
  ],
  template: `
    <div class="video-section">
      <h4><mat-icon>videocam</mat-icon> Video Output</h4>
      @if (isHdrFormat) {
        <p class="hdr-hint">Video output is not available for HDR formats.
          Download images as ZIP instead.</p>
      }
      <mat-checkbox [formControl]="generateVideoCtrl"
                    [disabled]="isHdrFormat">
        Generate Video
      </mat-checkbox>
      @if (generateVideoCtrl.value) {
        <div class="video-controls">
          <div class="form-row">
            <mat-form-field>
              <mat-label>Preset</mat-label>
              <mat-select [formControl]="presetCtrl"
                          (selectionChange)="onPresetChange()">
                @for (p of presetEntries; track p.key) {
                  <mat-option [value]="p.key">{{ p.label }}</mat-option>
                }
                <mat-option value="custom">Custom</mat-option>
              </mat-select>
            </mat-form-field>
            <mat-form-field>
              <mat-label>Framerate (fps)</mat-label>
              <input matInput type="number" [formControl]="framerateCtrl" />
            </mat-form-field>
          </div>
          <mat-expansion-panel [expanded]="presetCtrl.value === 'custom'">
            <mat-expansion-panel-header>
              <mat-panel-title>Advanced Settings</mat-panel-title>
            </mat-expansion-panel-header>
            <div class="form-row">
              <mat-form-field>
                <mat-label>Container</mat-label>
                <mat-select [formControl]="containerCtrl">
                  <mat-option value="mp4">MP4</mat-option>
                  <mat-option value="webm">WebM</mat-option>
                  <mat-option value="mov">MOV</mat-option>
                </mat-select>
              </mat-form-field>
              <mat-form-field>
                <mat-label>Codec</mat-label>
                <mat-select [formControl]="codecCtrl">
                  <mat-option value="libx264">H.264</mat-option>
                  <mat-option value="libx265">H.265</mat-option>
                  <mat-option value="libvpx-vp9">VP9</mat-option>
                  <mat-option value="prores_ks">ProRes</mat-option>
                </mat-select>
              </mat-form-field>
              <mat-form-field>
                <mat-label>CRF</mat-label>
                <input matInput type="number" [formControl]="crfCtrl" />
              </mat-form-field>
            </div>
          </mat-expansion-panel>
        </div>
      }
    </div>
  `,
  styles: [`
    .video-section { margin-top: 8px; }
    .video-section h4 { display: flex; align-items: center; gap: 6px; margin: 0 0 8px; }
    .hdr-hint { color: rgba(0,0,0,0.6); font-size: 13px; margin: 0 0 8px; }
    .video-controls { margin-top: 8px; }
    .form-row { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }
    .form-row mat-form-field { flex: 1; min-width: 120px; }
  `],
})
export class VideoOutputSectionComponent implements OnInit, OnDestroy {
  @Input() parentForm!: FormGroup;
  @Input() outputFormat = '';

  readonly presetEntries = Object.entries(VIDEO_PRESETS).map(
    ([key, val]) => ({ key, label: val.label }),
  );

  private subs: Subscription[] = [];

  get isHdrFormat(): boolean {
    return HDR_FORMATS.has(this.outputFormat);
  }

  get generateVideoCtrl() { return this.parentForm.get('generateVideo') as FormControl; }
  get presetCtrl() { return this.parentForm.get('videoPreset') as FormControl; }
  get framerateCtrl() { return this.parentForm.get('videoFramerate') as FormControl; }
  get containerCtrl() { return this.parentForm.get('videoContainer') as FormControl; }
  get codecCtrl() { return this.parentForm.get('videoCodec') as FormControl; }
  get crfCtrl() { return this.parentForm.get('videoCrf') as FormControl; }

  ngOnInit(): void {
    this.applyPreset(this.presetCtrl.value as string);
    this.updateAdvancedDisabledState();
  }

  ngOnDestroy(): void {
    this.subs.forEach(s => s.unsubscribe());
  }

  onPresetChange(): void {
    const preset = this.presetCtrl.value as string;
    this.applyPreset(preset);
    this.updateAdvancedDisabledState();
  }

  private applyPreset(preset: string): void {
    const config = VIDEO_PRESETS[preset];
    if (config) {
      this.containerCtrl.setValue(config.container);
      this.codecCtrl.setValue(config.codec);
      this.crfCtrl.setValue(config.crf);
    }
  }

  private updateAdvancedDisabledState(): void {
    const isCustom = this.presetCtrl.value === 'custom';
    if (isCustom) {
      this.containerCtrl.enable();
      this.codecCtrl.enable();
      this.crfCtrl.enable();
    } else {
      this.containerCtrl.disable();
      this.codecCtrl.disable();
      this.crfCtrl.disable();
    }
  }
}
