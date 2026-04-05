// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatTableModule } from '@angular/material/table';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { AssetService, Asset } from '../../core/services/asset.service';

@Component({
  selector: 'app-asset-list',
  standalone: true,
  imports: [
    CommonModule, MatTableModule, MatButtonModule,
    MatIconModule, MatProgressSpinnerModule,
  ],
  template: `
    <div class="header">
      <h1>Assets</h1>
      <button mat-raised-button color="primary" (click)="fileInput.click()">
        <mat-icon>upload</mat-icon> Upload Asset
      </button>
      <input #fileInput type="file" accept=".blend" hidden
             (change)="onFileSelected($event)" />
    </div>

    @if (loading) {
      <mat-spinner diameter="40" />
    } @else {
      <table mat-table [dataSource]="assets" class="full-width">
        <ng-container matColumnDef="name">
          <th mat-header-cell *matHeaderCellDef>Name</th>
          <td mat-cell *matCellDef="let a">{{ a.name }}</td>
        </ng-container>
        <ng-container matColumnDef="blend_file">
          <th mat-header-cell *matHeaderCellDef>File</th>
          <td mat-cell *matCellDef="let a">{{ a.blend_file }}</td>
        </ng-container>
        <ng-container matColumnDef="created_at">
          <th mat-header-cell *matHeaderCellDef>Uploaded</th>
          <td mat-cell *matCellDef="let a">{{ a.created_at | date }}</td>
        </ng-container>
        <tr mat-header-row *matHeaderRowDef="displayedColumns"></tr>
        <tr mat-row *matRowDef="let row; columns: displayedColumns"></tr>
      </table>
    }
  `,
  styles: [`
    .header { display: flex; justify-content: space-between; align-items: center; }
    .full-width { width: 100%; }
  `],
})
export class AssetListComponent implements OnInit {
  private readonly assetService = inject(AssetService);

  assets: Asset[] = [];
  loading = true;
  displayedColumns = ['name', 'blend_file', 'created_at'];

  ngOnInit(): void {
    this.assetService.list().subscribe({
      next: (assets) => { this.assets = assets; this.loading = false; },
      error: () => { this.loading = false; },
    });
  }

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (!input.files?.length) return;
    // TODO: Show project selection dialog before uploading
    console.log('File selected:', input.files[0].name);
  }
}
