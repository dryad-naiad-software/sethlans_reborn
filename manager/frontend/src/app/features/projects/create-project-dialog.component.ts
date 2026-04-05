// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject, OnInit } from '@angular/core';
import { ReactiveFormsModule, FormGroup, FormControl, Validators } from '@angular/forms';
import { MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { HttpEventType } from '@angular/common/http';
import { ProjectService, Project } from '../../core/services/project.service';
import { AssetService } from '../../core/services/asset.service';
import {
  SupportedVersionService,
  SupportedVersion,
} from '../../core/services/supported-version.service';

@Component({
  selector: 'app-create-project-dialog',
  standalone: true,
  imports: [
    ReactiveFormsModule, MatDialogModule, MatFormFieldModule,
    MatInputModule, MatSelectModule, MatButtonModule, MatIconModule,
    MatProgressBarModule, MatSnackBarModule,
  ],
  template: `
    <h2 mat-dialog-title>New Project</h2>
    <mat-dialog-content>
      <form [formGroup]="form" class="form-fields">
        <mat-form-field class="full-width">
          <mat-label>Project Name</mat-label>
          <input matInput formControlName="name" />
          @if (form.controls.name.hasError('minlength') ||
               form.controls.name.hasError('maxlength')) {
            <mat-hint>4 - 40 characters</mat-hint>
          }
        </mat-form-field>

        <mat-form-field class="full-width">
          <mat-label>Blender Version</mat-label>
          <mat-select formControlName="blenderVersion">
            @for (v of versions; track v.id) {
              <mat-option [value]="v.id">
                {{ v.series }} ({{ v.resolved_version }})
                @if (v.is_default) { — Default }
              </mat-option>
            }
          </mat-select>
        </mat-form-field>

        <div class="file-row">
          <button mat-stroked-button type="button" (click)="fileInput.click()"
                  [disabled]="uploading">
            <mat-icon>upload_file</mat-icon>
            {{ selectedFile ? selectedFile.name : 'Choose .blend file' }}
          </button>
          <input #fileInput type="file" accept=".blend" hidden
                 (change)="onFileSelected($event)" />
        </div>

        @if (uploading) {
          <div class="progress-section">
            <p>Uploading...</p>
            <mat-progress-bar mode="determinate" [value]="uploadProgress" />
            <p class="progress-label">{{ uploadProgress }}%</p>
          </div>
        }
      </form>
    </mat-dialog-content>

    <mat-dialog-actions align="end">
      <button mat-button mat-dialog-close [disabled]="uploading">Cancel</button>
      <button mat-flat-button color="primary"
              [disabled]="form.invalid || !selectedFile || uploading"
              (click)="onSubmit()">
        Create Project
      </button>
    </mat-dialog-actions>
  `,
  styles: [`
    .form-fields { display: flex; flex-direction: column; gap: 16px; min-width: 400px; }
    .full-width { width: 100%; }
    .file-row { display: flex; align-items: center; }
    .progress-section { margin-top: 8px; }
    .progress-label { text-align: right; font-size: 12px; color: rgba(0,0,0,0.6); }
  `],
})
export class CreateProjectDialogComponent implements OnInit {
  private readonly dialogRef = inject(MatDialogRef<CreateProjectDialogComponent>);
  private readonly projectService = inject(ProjectService);
  private readonly assetService = inject(AssetService);
  private readonly versionService = inject(SupportedVersionService);
  private readonly snackBar = inject(MatSnackBar);

  versions: SupportedVersion[] = [];
  selectedFile: File | null = null;
  uploading = false;
  uploadProgress = 0;

  form = new FormGroup({
    name: new FormControl('', [
      Validators.required,
      Validators.minLength(4),
      Validators.maxLength(40),
    ]),
    blenderVersion: new FormControl<number | null>(null, Validators.required),
  });

  ngOnInit(): void {
    this.versionService.list().subscribe({
      next: (versions) => {
        this.versions = versions;
        const defaultV = versions.find(v => v.is_default);
        if (defaultV) this.form.controls.blenderVersion.setValue(defaultV.id);
      },
      error: () => {
        this.snackBar.open('Failed to load Blender versions', 'Dismiss', { duration: 5000 });
      },
    });
  }

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (!input.files?.length) return;
    const file = input.files[0];
    if (!file.name.toLowerCase().endsWith('.blend')) {
      this.snackBar.open('Only .blend files are accepted', 'Dismiss', { duration: 5000 });
      return;
    }
    this.selectedFile = file;
  }

  onSubmit(): void {
    if (this.form.invalid || !this.selectedFile) return;
    this.uploading = true;
    this.uploadProgress = 0;

    const name = this.form.controls.name.value!;
    const blenderVersion = this.form.controls.blenderVersion.value!;

    this.projectService.create({ name, blender_version: blenderVersion }).subscribe({
      next: (project) => this.uploadAsset(project),
      error: (err) => {
        this.uploading = false;
        const msg = err.error?.name?.[0] || err.error?.detail || 'Failed to create project';
        this.snackBar.open(msg, 'Dismiss', { duration: 5000 });
      },
    });
  }

  private uploadAsset(project: Project): void {
    this.assetService
      .upload(project.id, this.selectedFile!.name, this.selectedFile!)
      .subscribe({
        next: (event) => {
          if (event.type === HttpEventType.UploadProgress && event.total) {
            this.uploadProgress = Math.round((100 * event.loaded) / event.total);
          }
          if (event.type === HttpEventType.Response) {
            this.uploading = false;
            this.dialogRef.close(project);
          }
        },
        error: (err) => {
          this.uploading = false;
          this.projectService.delete(project.id).subscribe();
          const msg = err.error?.blend_file?.[0] || err.error?.detail || 'Asset upload failed';
          this.snackBar.open(msg, 'Dismiss', { duration: 5000 });
        },
      });
  }
}
