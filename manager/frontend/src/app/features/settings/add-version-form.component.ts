// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, EventEmitter, Input, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSelectModule } from '@angular/material/select';

@Component({
  selector: 'app-add-version-form',
  standalone: true,
  imports: [
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatFormFieldModule,
    MatProgressSpinnerModule,
    MatSelectModule,
  ],
  template: `
    <mat-card>
      <mat-card-header>
        <mat-card-title>Add Blender Version</mat-card-title>
      </mat-card-header>
      <mat-card-content>
        <form (ngSubmit)="onSubmit()" class="add-form">
          <mat-form-field>
            <mat-label>Series</mat-label>
            <mat-select [(ngModel)]="selectedSeries" name="series"
                        required [disabled]="creating">
              @for (s of availableSeries; track s) {
                <mat-option [value]="s">Blender {{ s }}</mat-option>
              }
            </mat-select>
            @if (!cacheReady) {
              <mat-hint>Loading available versions...</mat-hint>
            } @else if (availableSeries.length === 0) {
              <mat-hint>All available series already added</mat-hint>
            }
          </mat-form-field>
          <button mat-raised-button color="primary" type="submit"
                  [disabled]="creating || !selectedSeries">
            @if (creating) {
              <mat-spinner diameter="20" />
            } @else {
              Add Version
            }
          </button>
        </form>
      </mat-card-content>
    </mat-card>
  `,
  styles: [`
    .add-form {
      display: flex;
      align-items: baseline;
      gap: 16px;
    }
    mat-form-field { flex: 0 0 200px; }
  `],
})
export class AddVersionFormComponent {
  @Input() creating = false;
  @Input() availableSeries: string[] = [];
  @Input() cacheReady = true;
  @Output() addVersion = new EventEmitter<{ series: string; isDefault: boolean }>();

  selectedSeries = '';

  onSubmit(): void {
    if (!this.selectedSeries || this.creating) return;
    this.addVersion.emit({ series: this.selectedSeries, isDefault: false });
    this.selectedSeries = '';
  }
}
