// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, OnDestroy, OnInit, ViewChild, inject } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatRadioModule } from '@angular/material/radio';
import { MatSelectModule } from '@angular/material/select';
import { MatSnackBar } from '@angular/material/snack-bar';
import { Subscription, merge } from 'rxjs';
import { Asset } from '../../core/services/asset.service';
import { Project } from '../../core/services/project.service';
import { SequenceService, sequenceErrorText } from '../../core/services/sequence.service';
import { DiscoveryKey, SequenceDiscovery, SequenceSubmission } from '../../core/services/sequence.types';
import { VIDEO_PRESETS } from './render-payload.util';
import { SequenceFormValues, buildSubmissionPayload } from './sequence-submit-payload.util';
import { SequenceShotsPickerComponent } from './sequence-shots-picker.component';
import { submissionProblems } from './sequence-submit-validation.util';

export interface SequenceSubmitDialogData {
  project: Project;
  /** The project's assets, newest first. */
  assets: Asset[];
  /** Fills asset, map, sequence and preset; `shots` ticks only those after "Read shots". */
  prefill?: Partial<DiscoveryKey> & { shots?: string[] };
}

const SIZE = [Validators.min(64), Validators.max(16384)];

/** FR-SU-2: the basic sequence submit form. */
@Component({
  selector: 'app-sequence-submit-dialog',
  standalone: true,
  imports: [
    ReactiveFormsModule, MatDialogModule, MatButtonModule, MatCheckboxModule, MatFormFieldModule,
    MatInputModule, MatRadioModule, MatSelectModule, SequenceShotsPickerComponent,
  ],
  template: `
    <h2 mat-dialog-title>Render sequence</h2>
    <mat-dialog-content [formGroup]="form">
      <mat-form-field class="wide"><mat-label>Asset</mat-label>
        <mat-select formControlName="asset">
          @for (a of data.assets; track a.id) { <mat-option [value]="a.id">{{ a.name }}</mat-option> }
        </mat-select></mat-form-field>
      <mat-form-field class="wide"><mat-label>Map</mat-label><input matInput formControlName="map" /></mat-form-field>
      <mat-form-field class="wide"><mat-label>Sequence</mat-label><input matInput formControlName="sequence" /></mat-form-field>
      <mat-form-field class="wide"><mat-label>Preset</mat-label><input matInput formControlName="preset" /></mat-form-field>
      <app-sequence-shots-picker #picker [key]="key()" [preselect]="data.prefill?.shots ?? null"
        (discoveryChange)="onDiscovery($event)" (selectedChange)="shots = $event" />
      <mat-radio-group formControlName="selection" class="column">
        <mat-radio-button value="all">Whole sequence</mat-radio-button>
        <mat-radio-button value="shots" [disabled]="discovery?.status !== 'DONE'">Ticked shots</mat-radio-button>
        <mat-radio-button value="range">Frame range</mat-radio-button>
      </mat-radio-group>
      @if (form.value.selection === 'range') {
        <mat-form-field><mat-label>First frame</mat-label><input matInput type="number" formControlName="frameStart" /></mat-form-field>
        <mat-form-field><mat-label>Last frame</mat-label><input matInput type="number" formControlName="frameEnd" /></mat-form-field>
      }
      <h3>Output</h3>
      <mat-form-field><mat-label>Width</mat-label><input matInput type="number" formControlName="resolutionX" /></mat-form-field>
      <mat-form-field><mat-label>Height</mat-label><input matInput type="number" formControlName="resolutionY" /></mat-form-field>
      @if (presetSize(); as size) { <p>Blank uses the preset's size: {{ size }}</p> }
      <mat-radio-group formControlName="frameFormat">
        <mat-radio-button value="PNG">PNG</mat-radio-button>
        <mat-radio-button value="EXR" [disabled]="facts()?.can_exr === false">EXR</mat-radio-button>
      </mat-radio-group>
      @if (facts()?.can_exr === false) { <p>The preset has no EXR output.</p> }
      <mat-form-field class="wide"><mat-label>Video</mat-label>
        <mat-select formControlName="videoPreset">
          <mat-option value="">No video</mat-option>
          @for (p of presets; track p.key) { <mat-option [value]="p.key">{{ p.label }}</mat-option> }
        </mat-select></mat-form-field>
      <div class="column">
        <mat-checkbox formControlName="sound">Sound</mat-checkbox>
        @if (facts()?.can_sound === false) { <p>The preset has no audio output.</p> }
        <mat-checkbox formControlName="mergeSound">Merge sound into the videos</mat-checkbox>
        <mat-checkbox formControlName="draft">Draft</mat-checkbox>
      </div>
      @if (form.value.draft) {
        <mat-form-field><mat-label>Temporal samples</mat-label><input matInput type="number" formControlName="temporalSamples" /></mat-form-field>
        <mat-form-field><mat-label>Spatial samples</mat-label><input matInput type="number" formControlName="spatialSamples" /></mat-form-field>
        <mat-form-field><mat-label>Screen percentage</mat-label><input matInput type="number" formControlName="screenPercentage" /></mat-form-field>
        <div class="column">
          <mat-checkbox formControlName="motionBlurOff">Motion blur off</mat-checkbox>
          <mat-checkbox formControlName="heavyEffectsOff">Ray-traced shadows and reflections off</mat-checkbox>
        </div>
      }
      @for (problem of problems(); track problem) { <p class="form-error">{{ problem }}</p> }
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button mat-dialog-close type="button">Cancel</button>
      <button mat-raised-button color="primary" type="button" class="submit"
              (click)="submit()" [disabled]="!canSubmit() || submitting">Submit</button>
    </mat-dialog-actions>
  `,
  styles: [`.wide { width: 100%; } .column { display: flex; flex-direction: column; margin: 8px 0; }`],
})
export class SequenceSubmitDialogComponent implements OnInit, OnDestroy {
  @ViewChild('picker') picker?: SequenceShotsPickerComponent;
  readonly data = inject<SequenceSubmitDialogData>(MAT_DIALOG_DATA);
  private readonly dialogRef = inject(MatDialogRef<SequenceSubmitDialogComponent, SequenceSubmission>);
  private readonly sequences = inject(SequenceService);
  private readonly snackBar = inject(MatSnackBar);
  private readonly fb = inject(FormBuilder).nonNullable;
  private subs: Subscription[] = [];

  readonly presets = Object.entries(VIDEO_PRESETS).map(([key, v]) => ({ key, label: v.label }));
  readonly form = this.fb.group({
    asset: [this.data.prefill?.asset ?? this.data.assets[0]?.id ?? 0, Validators.required],
    map: [this.data.prefill?.map ?? '', Validators.required],
    sequence: [this.data.prefill?.sequence ?? '', Validators.required],
    preset: [this.data.prefill?.preset ?? '', Validators.required],
    selection: ['all' as SequenceFormValues['selection']],
    frameStart: [null as number | null, Validators.min(0)],
    frameEnd: [null as number | null, Validators.min(0)],
    resolutionX: [null as number | null, SIZE],
    resolutionY: [null as number | null, SIZE],
    frameFormat: ['PNG' as 'PNG' | 'EXR'],
    videoPreset: [''],
    sound: [this.data.project.unreal_sound ?? false],
    mergeSound: [false],
    draft: [false],
    temporalSamples: [null as number | null, Validators.min(1)],
    spatialSamples: [null as number | null, Validators.min(1)],
    screenPercentage: [null as number | null, [Validators.min(10), Validators.max(100)]],
    motionBlurOff: [false],
    heavyEffectsOff: [false],
  });
  discovery: SequenceDiscovery | null = null;
  shots: string[] = [];
  submitting = false;

  ngOnInit(): void {
    const c = this.form.controls;
    this.syncDisabled();
    this.subs.push(this.form.valueChanges.subscribe(() => this.syncDisabled()));
    this.subs.push(merge(c.asset.valueChanges, c.map.valueChanges, c.sequence.valueChanges, c.preset.valueChanges)
      .subscribe(() => {
        this.picker?.reset();
        if (c.selection.value === 'shots') c.selection.setValue('all');
      }));
    if (this.data.prefill?.shots) {
      c.selection.setValue('shots');
      setTimeout(() => this.picker?.read());
    } else if (!this.data.prefill?.map) {
      this.prefillFromProject();
    }
  }

  ngOnDestroy(): void { this.subs.forEach(s => s.unsubscribe()); }

  onDiscovery(discovery: SequenceDiscovery | null): void {
    this.discovery = discovery;
    if (this.facts()?.can_exr === false && this.form.controls.frameFormat.value === 'EXR') {
      this.form.controls.frameFormat.setValue('PNG');
    }
    this.syncDisabled();
  }

  key(): DiscoveryKey | null {
    const v = this.form.getRawValue();
    if (!v.asset || !v.map.trim() || !v.sequence.trim() || !v.preset.trim()) return null;
    return { asset: v.asset, map: v.map.trim(), sequence: v.sequence.trim(), preset: v.preset.trim() };
  }

  facts() { return this.discovery?.status === 'DONE' ? this.discovery.preset_facts : null; }

  presetSize(): string | null {
    const size = this.facts()?.resolution;
    return size ? `${size[0]}x${size[1]}` : null;
  }

  canMerge(): boolean {
    const v = this.form.getRawValue();
    return v.sound && this.facts()?.can_sound !== false && v.frameFormat === 'PNG' && v.videoPreset !== '';
  }

  /** Reactive controls are disabled in code; a template [disabled] would not apply to them. */
  private syncDisabled(): void {
    const c = this.form.controls;
    const set = (ctrl: { enabled: boolean; disable(o: object): void; enable(o: object): void }, off: boolean) => {
      if (off && ctrl.enabled) ctrl.disable({ emitEvent: false });
      else if (!off && !ctrl.enabled) ctrl.enable({ emitEvent: false });
    };
    set(c.videoPreset, c.frameFormat.value === 'EXR');
    set(c.sound, this.facts()?.can_sound === false);
    set(c.mergeSound, !this.canMerge());
  }

  /** What the API would refuse, as plain messages (blocks Submit). */
  problems(): string[] { return submissionProblems(this.form.getRawValue(), this.discovery); }

  canSubmit(): boolean {
    const v = this.form.getRawValue();
    if (this.form.invalid || this.key() === null || this.problems().length > 0) return false;
    if ((v.resolutionX === null) !== (v.resolutionY === null)) return false;
    if (v.selection === 'shots') return this.shots.length > 0;
    if (v.selection === 'range') return v.frameStart !== null && v.frameEnd !== null && v.frameStart <= v.frameEnd;
    return true;
  }

  submit(): void {
    if (!this.canSubmit()) return;
    const values: SequenceFormValues = this.form.getRawValue();
    values.sound = values.sound && this.facts()?.can_sound !== false;
    values.mergeSound = values.mergeSound && this.canMerge();
    this.submitting = true;
    this.sequences.create(buildSubmissionPayload(values, this.shots)).subscribe({
      next: created => {
        this.snackBar.open(`Submitted ${created.name}`, 'Dismiss', { duration: 3000 });
        this.dialogRef.close(created);
      },
      error: (error: unknown) => {
        this.submitting = false;
        this.snackBar.open(sequenceErrorText(error, 'Failed to submit'), 'Dismiss', { duration: 8000 });
      },
    });
  }

  /** FR-SU-2: map, sequence and preset from the project's last submission, else its last discovery. */
  private prefillFromProject(): void {
    const fill = (k: Partial<DiscoveryKey>): void => {
      if (this.form.controls.map.value) return;
      this.form.patchValue({ map: k.map ?? '', sequence: k.sequence ?? '', preset: k.preset ?? '' });
    };
    const project = this.data.project.id;
    this.subs.push(this.sequences.list(project).subscribe({
      next: versions => {
        if (versions.length > 0) { fill(versions[0]); return; }
        this.subs.push(this.sequences.listDiscoveries(project).subscribe({
          next: found => { if (found.length > 0) fill(found[0]); },
          error: () => undefined,
        }));
      },
      error: () => undefined,
    }));
  }
}
