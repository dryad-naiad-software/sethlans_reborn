import { Component, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { DomSanitizer, SafeUrl } from '@angular/platform-browser';
import { MatTableModule } from '@angular/material/table';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { Subscription } from 'rxjs';
import { WorkerService, Worker } from '../../core/services/worker.service';

interface WorkerRow extends Worker {
  sanitizedUiUrl: SafeUrl | null;
}

@Component({
  selector: 'app-worker-list',
  standalone: true,
  imports: [
    CommonModule, MatTableModule, MatButtonModule,
    MatIconModule, MatProgressSpinnerModule,
  ],
  template: `
    <h1>Workers</h1>

    @if (loading) {
      <mat-spinner diameter="40" />
    } @else {
      <table mat-table [dataSource]="workers" class="full-width">
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
          <td mat-cell *matCellDef="let w">{{ w.gpu_name }}</td>
        </ng-container>
        <ng-container matColumnDef="ui_url">
          <th mat-header-cell *matHeaderCellDef>Worker UI</th>
          <td mat-cell *matCellDef="let w">
            @if (w.sanitizedUiUrl) {
              <a [href]="w.sanitizedUiUrl" target="_blank" rel="noopener">
                <button mat-icon-button>
                  <mat-icon>open_in_new</mat-icon>
                </button>
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
        <tr mat-header-row *matHeaderRowDef="displayedColumns"></tr>
        <tr mat-row *matRowDef="let row; columns: displayedColumns"></tr>
      </table>
    }
  `,
  styles: [`
    .full-width { width: 100%; }
    .no-ui { color: #999; }
  `],
})
export class WorkerListComponent implements OnInit, OnDestroy {
  private readonly workerService = inject(WorkerService);
  private readonly sanitizer = inject(DomSanitizer);
  private sub?: Subscription;

  workers: WorkerRow[] = [];
  loading = true;
  displayedColumns = [
    'hostname', 'ip_address', 'status', 'cpu_name',
    'gpu_name', 'ui_url', 'last_heartbeat',
  ];

  ngOnInit(): void {
    this.sub = this.workerService.pollList().subscribe({
      next: (workers) => {
        this.workers = workers.map(w => ({
          ...w,
          sanitizedUiUrl: this.sanitizeUrl(w.ui_url),
        }));
        this.loading = false;
      },
      error: () => { this.loading = false; },
    });
  }

  ngOnDestroy(): void { this.sub?.unsubscribe(); }

  /**
   * Sanitizes worker UI URL, only allowing http:// and https:// schemes.
   * Returns null for invalid or disallowed URLs.
   */
  private sanitizeUrl(url: string | null): SafeUrl | null {
    if (!url) return null;
    try {
      const parsed = new URL(url);
      if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
        return this.sanitizer.bypassSecurityTrustUrl(url);
      }
      return null;
    } catch {
      return null;
    }
  }
}
