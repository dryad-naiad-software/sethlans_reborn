// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, EventEmitter, Input, Output } from '@angular/core';
import { DatePipe } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTableModule } from '@angular/material/table';
import {
  SupportedVersion,
  DeletePreview,
} from '../../core/services/supported-version.service';

@Component({
  selector: 'app-version-table',
  standalone: true,
  imports: [
    DatePipe,
    MatButtonModule,
    MatCardModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatTableModule,
  ],
  template: `
    @if (versions.length === 0) {
      <p class="empty">No supported Blender versions configured.</p>
    } @else {
      <table mat-table [dataSource]="versions">
        <ng-container matColumnDef="series">
          <th mat-header-cell *matHeaderCellDef>Series</th>
          <td mat-cell *matCellDef="let v">{{ v.series }}</td>
        </ng-container>

        <ng-container matColumnDef="resolved_version">
          <th mat-header-cell *matHeaderCellDef>Resolved Version</th>
          <td mat-cell *matCellDef="let v">{{ v.resolved_version }}</td>
        </ng-container>

        <ng-container matColumnDef="is_default">
          <th mat-header-cell *matHeaderCellDef>Default</th>
          <td mat-cell *matCellDef="let v">
            <mat-icon>{{ v.is_default ? 'star' : 'star_border' }}</mat-icon>
          </td>
        </ng-container>

        <ng-container matColumnDef="added_at">
          <th mat-header-cell *matHeaderCellDef>Added</th>
          <td mat-cell *matCellDef="let v">{{ v.added_at | date:'mediumDate' }}</td>
        </ng-container>

        <ng-container matColumnDef="actions">
          <th mat-header-cell *matHeaderCellDef>Actions</th>
          <td mat-cell *matCellDef="let v">
            @if (!v.is_default) {
              <button mat-icon-button (click)="setDefault.emit(v.id)"
                      [disabled]="settingDefaultId !== null"
                      title="Set as default">
                @if (settingDefaultId === v.id) {
                  <mat-spinner diameter="20" />
                } @else {
                  <mat-icon>star_border</mat-icon>
                }
              </button>
            }
            @if (versions.length > 1) {
              <button mat-icon-button color="warn"
                      (click)="requestDelete.emit(v.id)"
                      [disabled]="deletingId !== null || expandedDeleteId !== null"
                      title="Remove version">
                @if (deletingId === v.id) {
                  <mat-spinner diameter="20" />
                } @else {
                  <mat-icon>delete</mat-icon>
                }
              </button>
            }
          </td>
        </ng-container>

        <tr mat-header-row *matHeaderRowDef="displayedColumns"></tr>
        <tr mat-row *matRowDef="let row; columns: displayedColumns;"></tr>
      </table>
    }

    @if (expandedDeleteId !== null && deletePreview) {
      <mat-card class="delete-preview">
        <mat-card-header>
          <mat-card-title>Confirm Removal</mat-card-title>
        </mat-card-header>
        <mat-card-content>
          <p>{{ deletePreview.warning }}</p>
          <p><strong>Affected projects:</strong> {{ deletePreview.affected_project_count }}</p>
          <p><strong>Affected jobs:</strong> {{ deletePreview.affected_job_count }}</p>
          @if (deletePreview.migration_target) {
            <p><strong>Migration target:</strong>
              {{ deletePreview.migration_target.series }}
              ({{ deletePreview.migration_target.resolved_version }})</p>
          }
        </mat-card-content>
        <mat-card-actions>
          <button mat-button (click)="cancelDelete.emit()">Cancel</button>
          <button mat-raised-button color="warn"
                  [disabled]="deletingId !== null"
                  (click)="confirmDelete.emit(expandedDeleteId)">
            @if (deletingId !== null) {
              <mat-spinner diameter="20" />
            } @else {
              Confirm Removal
            }
          </button>
        </mat-card-actions>
      </mat-card>
    }
  `,
  styles: [`
    table { width: 100%; }
    .empty { color: #999; text-align: center; padding: 24px; }
    .delete-preview { margin-top: 16px; }
    mat-card-actions { display: flex; gap: 8px; justify-content: flex-end; }
  `],
})
export class VersionTableComponent {
  @Input() versions: SupportedVersion[] = [];
  @Input() expandedDeleteId: number | null = null;
  @Input() deletePreview: DeletePreview | null = null;
  @Input() deletingId: number | null = null;
  @Input() settingDefaultId: number | null = null;

  @Output() setDefault = new EventEmitter<number>();
  @Output() requestDelete = new EventEmitter<number>();
  @Output() confirmDelete = new EventEmitter<number>();
  @Output() cancelDelete = new EventEmitter<void>();

  displayedColumns = ['series', 'resolved_version', 'is_default', 'added_at', 'actions'];
}
