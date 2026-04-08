// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, Input, OnInit, OnDestroy } from '@angular/core';
import { ReactiveFormsModule, FormGroup, FormControl } from '@angular/forms';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatRadioModule } from '@angular/material/radio';
import { Subscription } from 'rxjs';
import {
  RESOLUTION_PRESETS, ResolutionGroup, ResolutionPreset,
  PRESET_GROUP_LABELS, findPresetByXY,
} from './resolution-presets';

@Component({
  selector: 'app-resolution-input',
  standalone: true,
  imports: [
    ReactiveFormsModule, MatFormFieldModule, MatInputModule,
    MatSelectModule, MatRadioModule,
  ],
  template: `
    <div class="resolution-input">
      <div class="resolution-header">
        <label id="resolution-label" class="resolution-label">Render Resolution</label>
        <mat-radio-group [formControl]="modeCtrl"
                         aria-labelledby="resolution-label"
                         class="resolution-mode">
          <mat-radio-button value="preset">Use Preset</mat-radio-button>
          <mat-radio-button value="custom">Custom</mat-radio-button>
        </mat-radio-group>
      </div>

      <mat-form-field class="preset-select">
        <mat-label>Preset</mat-label>
        <mat-select [formControl]="presetCtrl">
          @for (group of groupOrder; track group) {
            <mat-optgroup [label]="groupLabels[group]">
              @for (preset of presetsByGroup[group]; track preset.id) {
                <mat-option [value]="preset.id">
                  {{ preset.label }} — {{ preset.x }}×{{ preset.y }}
                </mat-option>
              }
            </mat-optgroup>
          }
        </mat-select>
      </mat-form-field>

      <div class="resolution-xy">
        <mat-form-field>
          <mat-label>Resolution X</mat-label>
          <input matInput type="number" [formControl]="resolutionXCtrl" />
        </mat-form-field>
        <mat-form-field>
          <mat-label>Resolution Y</mat-label>
          <input matInput type="number" [formControl]="resolutionYCtrl" />
        </mat-form-field>
      </div>
    </div>
  `,
  styles: [`
    .resolution-input { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
    .resolution-header { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
    .resolution-label { font-weight: 500; }
    .resolution-mode { display: flex; gap: 16px; }
    .preset-select { width: 100%; }
    .resolution-xy { display: flex; flex-direction: column; gap: 8px; }
    .resolution-xy mat-form-field { width: 100%; }
  `],
})
export class ResolutionInputComponent implements OnInit, OnDestroy {
  @Input() parentForm!: FormGroup;

  readonly groupOrder: ResolutionGroup[] = ['horizontal', 'cinema', 'vertical', 'square'];
  readonly groupLabels = PRESET_GROUP_LABELS;
  readonly presetsByGroup: Record<ResolutionGroup, ResolutionPreset[]> = {
    horizontal: RESOLUTION_PRESETS.filter(p => p.group === 'horizontal'),
    cinema:     RESOLUTION_PRESETS.filter(p => p.group === 'cinema'),
    vertical:   RESOLUTION_PRESETS.filter(p => p.group === 'vertical'),
    square:     RESOLUTION_PRESETS.filter(p => p.group === 'square'),
  };

  private subs: Subscription[] = [];

  get modeCtrl() {
    return this.parentForm.get('resolutionMode') as FormControl<'preset' | 'custom'>;
  }
  get presetCtrl() {
    return this.parentForm.get('resolutionPreset') as FormControl<string>;
  }
  get resolutionXCtrl() {
    return this.parentForm.get('resolutionX') as FormControl<number | null>;
  }
  get resolutionYCtrl() {
    return this.parentForm.get('resolutionY') as FormControl<number | null>;
  }

  ngOnInit(): void {
    const x = this.resolutionXCtrl.value;
    const y = this.resolutionYCtrl.value;
    const match = (typeof x === 'number' && typeof y === 'number')
      ? findPresetByXY(x, y)
      : null;

    if (match) {
      this.presetCtrl.setValue(match.id, { emitEvent: false });
      this.modeCtrl.setValue('preset', { emitEvent: false });
    } else {
      this.modeCtrl.setValue('custom', { emitEvent: false });
    }

    this.updatePresetSelectDisabledState();
    this.updateResolutionXYDisabledState();

    this.subs.push(this.modeCtrl.valueChanges.subscribe(() => {
      this.updatePresetSelectDisabledState();
      this.updateResolutionXYDisabledState();
      if (this.modeCtrl.value === 'preset') {
        this.applySelectedPreset();
      }
    }));

    this.subs.push(this.presetCtrl.valueChanges.subscribe(() => {
      if (this.modeCtrl.value !== 'preset') return;
      this.applySelectedPreset();
    }));
  }

  ngOnDestroy(): void {
    this.subs.forEach(s => s.unsubscribe());
  }

  private updatePresetSelectDisabledState(): void {
    if (this.modeCtrl.value === 'custom') {
      this.presetCtrl.disable({ emitEvent: false });
    } else {
      this.presetCtrl.enable({ emitEvent: false });
    }
  }

  private updateResolutionXYDisabledState(): void {
    if (this.modeCtrl.value === 'preset') {
      this.resolutionXCtrl.disable({ emitEvent: false });
      this.resolutionYCtrl.disable({ emitEvent: false });
    } else {
      this.resolutionXCtrl.enable({ emitEvent: false });
      this.resolutionYCtrl.enable({ emitEvent: false });
    }
  }

  private applySelectedPreset(): void {
    const preset = RESOLUTION_PRESETS.find(p => p.id === this.presetCtrl.value);
    if (!preset) return;
    this.resolutionXCtrl.setValue(preset.x, { emitEvent: false });
    this.resolutionYCtrl.setValue(preset.y, { emitEvent: false });
  }
}
