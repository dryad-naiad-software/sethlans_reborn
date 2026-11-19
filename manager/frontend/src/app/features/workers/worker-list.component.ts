// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatTableModule } from '@angular/material/table';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { Subscription } from 'rxjs';
import { WorkerService } from '../../core/services/worker.service';
import { WorkerCardsComponent } from './worker-cards.component';
import { WorkerCardPatch, WorkerRow, toRow } from './worker-row.util';

@Component({
  selector: 'app-worker-list',
  standalone: true,
  imports: [
    CommonModule, MatTableModule, MatButtonModule, MatIconModule,
    MatChipsModule, MatProgressSpinnerModule, WorkerCardsComponent,
  ],
  template: `
    <h1>Workers</h1>
    <p class="update-failed" role="status">
      @if (updateFailed && !loading) { Last update failed. Retrying. }
    </p>

    @if (loading) {
      <mat-spinner diameter="40" />
    } @else {
      <table mat-table [dataSource]="workers" [trackBy]="trackById"
             multiTemplateDataRows class="full-width">
        <ng-container matColumnDef="expand">
          <th mat-header-cell *matHeaderCellDef><span class="cdk-visually-hidden">Cards</span></th>
          <td mat-cell *matCellDef="let w">
            <button mat-icon-button type="button" class="expand-toggle" (click)="toggle(w.id)"
                    [attr.aria-expanded]="expandedIds.has(w.id)" [attr.aria-controls]="'worker-cards-' + w.id"
                    [attr.aria-label]="(expandedIds.has(w.id) ? 'Hide' : 'Show') + ' graphics cards of ' + w.hostname">
              <mat-icon>{{ expandedIds.has(w.id) ? 'expand_less' : 'expand_more' }}</mat-icon>
            </button>
          </td>
        </ng-container>
        <ng-container matColumnDef="hostname">
          <th mat-header-cell *matHeaderCellDef>Hostname</th>
          <td mat-cell *matCellDef="let w">{{ w.hostname }}</td>
        </ng-container>
        <ng-container matColumnDef="ip_address">
          <th mat-header-cell *matHeaderCellDef>IP Address</th>
          <td mat-cell *matCellDef="let w">{{ w.ip_address }}</td>
        </ng-container>
        <ng-container matColumnDef="status">
          <th mat-header-cell *matHeaderCellDef>Status</th>
          <td mat-cell *matCellDef="let w">{{ w.status }}</td>
        </ng-container>
        <ng-container matColumnDef="cpu_name">
          <th mat-header-cell *matHeaderCellDef>CPU</th>
          <td mat-cell *matCellDef="let w">{{ w.cpu_name }}</td>
        </ng-container>
        <ng-container matColumnDef="gpu_name">
          <th mat-header-cell *matHeaderCellDef>GPU</th>
          <td mat-cell *matCellDef="let w">
            <span class="gpu-text">{{ w.gpuText }}</span>
            @if (w.alertCount > 0 || w.pending) {
              <mat-chip-set class="card-chips">
                @if (w.alertCount > 0) {
                  <mat-chip class="card-alert-chip">Card alert ({{ w.alertCount }})</mat-chip>
                }
                @if (w.pending) {
                  <mat-chip class="change-pending-chip">Change pending</mat-chip>
                }
              </mat-chip-set>
            }
          </td>
        </ng-container>
        <ng-container matColumnDef="ui_url">
          <th mat-header-cell *matHeaderCellDef>Worker UI</th>
          <td mat-cell *matCellDef="let w">
            @if (w.uiUrl) {
              <a mat-icon-button [href]="w.uiUrl" target="_blank" rel="noopener noreferrer"
                 [attr.aria-label]="'Open the worker page of ' + w.hostname">
                <mat-icon>open_in_new</mat-icon>
              </a>
            } @else {
              <span class="no-ui">N/A</span>
            }
          </td>
        </ng-container>
        <ng-container matColumnDef="last_heartbeat">
          <th mat-header-cell *matHeaderCellDef>Last Heartbeat</th>
          <td mat-cell *matCellDef="let w">{{ w.last_heartbeat | date:'medium' }}</td>
        </ng-container>
        <ng-container matColumnDef="cards">
          <td mat-cell *matCellDef="let w" class="cards-cell" [id]="'worker-cards-' + w.id"
              [attr.colspan]="displayedColumns.length">
            @if (expandedIds.has(w.id)) {
              <app-worker-cards [worker]="w" [uiUrl]="w.uiUrl"
                                (cardsUpdated)="applyCardUpdate(w.id, $event)" />
            }
          </td>
        </ng-container>
        <tr mat-header-row *matHeaderRowDef="displayedColumns"></tr>
        <tr mat-row *matRowDef="let w; columns: displayedColumns" class="worker-row"
            [class.card-alert-row]="w.alertCount > 0"></tr>
        <tr mat-row *matRowDef="let w; columns: detailColumns" class="cards-row"
            [class.collapsed]="!expandedIds.has(w.id)"></tr>
      </table>
    }
  `,
  styles: [`
    .full-width { width: 100%; }
    .no-ui { color: #999; }
    .update-failed { color: #c62828; margin: 0 0 8px; min-height: 1.25em; }
    .card-alert-row > td:first-child { box-shadow: inset 4px 0 0 #c62828; }
    .card-chips { display: inline-block; margin-left: 8px; vertical-align: middle; }
    .card-alert-chip {
      --mat-chip-elevated-container-color: #c62828;
      --mat-chip-label-text-color: #fff;
      --mat-chip-outline-color: #c62828;
    }
    .change-pending-chip {
      --mat-chip-elevated-container-color: #e3f2fd;
      --mat-chip-label-text-color: #0d47a1;
    }
    .cards-row.collapsed { height: 0; }
    .cards-row.collapsed > .cards-cell { padding-top: 0; padding-bottom: 0; }
    .worker-row > td { border-bottom-width: 0; }
  `],
})
export class WorkerListComponent implements OnInit, OnDestroy {
  private readonly workerService = inject(WorkerService);
  private sub?: Subscription;

  workers: WorkerRow[] = [];
  loading = true;
  /** True after a failed poll until the next successful one; the rows stay as they were (#261). */
  updateFailed = false;
  /** Ids of the expanded workers; kept across polls (R1). */
  readonly expandedIds = new Set<number>();
  displayedColumns = [
    'expand', 'hostname', 'ip_address', 'status', 'cpu_name',
    'gpu_name', 'ui_url', 'last_heartbeat',
  ];
  readonly detailColumns = ['cards'];

  /** Rows are matched by worker id, so a poll's new objects keep the row views and their children. */
  readonly trackById = (_index: number, row: WorkerRow): number => row.id;

  ngOnInit(): void {
    const failed = (): void => { this.loading = false; this.updateFailed = true; };
    this.sub = this.workerService.pollList(failed).subscribe({
      next: (workers) => {
        this.workers = workers.map(w => toRow(w));
        this.pruneExpanded();
        this.loading = false;
        this.updateFailed = false;
      },
      // Only a 401/403 ends polling; nothing retries then, so no "Retrying" note.
      error: () => { this.loading = false; this.updateFailed = false; },
    });
  }

  ngOnDestroy(): void { this.sub?.unsubscribe(); }

  toggle(id: number): void {
    if (this.expandedIds.has(id)) {
      this.expandedIds.delete(id);
    } else {
      this.expandedIds.add(id);
    }
  }

  /** Applies a card action's answer to its row at once, without waiting for the next poll. */
  applyCardUpdate(id: number, patch: WorkerCardPatch): void {
    this.workers = this.workers.map(w => w.id === id ? toRow({ ...w, ...patch }) : w);
  }

  private pruneExpanded(): void {
    const ids = new Set(this.workers.map(w => w.id));
    for (const id of [...this.expandedIds]) {
      if (!ids.has(id)) this.expandedIds.delete(id);
    }
  }
}
