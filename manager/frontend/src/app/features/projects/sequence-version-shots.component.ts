// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, EventEmitter, Input, OnDestroy, OnInit, Output, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatSnackBar } from '@angular/material/snack-bar';
import { Subscription, takeWhile } from 'rxjs';
import { SequenceService, sequenceErrorText } from '../../core/services/sequence.service';
import { SequenceShot, SequenceSubmission } from '../../core/services/sequence.types';
import { SequenceDownloadsComponent } from './sequence-downloads.component';
import { isActive } from './sequence-actions.util';
import { MachineRowsComponent } from '../../shared/machine-rows.component';

/**
 * The shots of one version (FR-SU-3), from the detail view, polled every
 * 10 s while the row is open and the version is active (restarted by
 * "Render again"): name, frames, status, errors, downloads,
 * "Render again" for a failed or canceled shot and "New version of this
 * shot", then the piece key's machine rows (per-machine memory test
 * FR-MT-U-4). Shot names and errors are interpolated only (FR-SU-4b).
 */
@Component({
  selector: 'app-sequence-version-shots',
  standalone: true,
  imports: [MatButtonModule, SequenceDownloadsComponent, MachineRowsComponent],
  template: `
    @if (!detail) {
      <p>Loading shots...</p>
    } @else {
      <table class="shots">
        <thead><tr><th>Shot</th><th>Frames</th><th>Status</th><th>Downloads</th><th></th></tr></thead>
        <tbody>
          @for (shot of detail.shots ?? []; track shot.index) {
            <tr class="shot">
              <td class="shot-name">{{ shot.name }}</td>
              <td>{{ shot.start_frame }}-{{ shot.end_frame - 1 }}</td>
              <td>
                <span class="shot-status">{{ shot.status }}</span>
                @for (problem of problems(shot); track $index) { <div class="shot-error">{{ problem }}</div> }
              </td>
              <td>
                <app-sequence-downloads [submissionId]="detail.id" [shotIndex]="shot.index"
                  [downloads]="shot.downloads" />
              </td>
              <td>
                @if (shot.status === 'ERROR' || shot.status === 'CANCELED') {
                  <button mat-button class="rerender" (click)="rerender(shot)" [disabled]="busy">Render again</button>
                }
                <button mat-button class="new-version" (click)="newVersion.emit(shot.name)">New version of this shot</button>
              </td>
            </tr>
          }
        </tbody>
      </table>
      <app-machine-rows [machines]="detail.machines" />
    }
  `,
  styles: [`.shots { width: 100%; border-collapse: collapse; } .shots td, .shots th { text-align: left; padding: 2px 6px; }
    .shot-error { color: #b00020; font-size: 12px; }`],
})
export class SequenceVersionShotsComponent implements OnInit, OnDestroy {
  private readonly sequences = inject(SequenceService);
  private readonly snackBar = inject(MatSnackBar);
  private sub?: Subscription;

  @Input({ required: true }) submissionId!: number;
  /** A shot's name, to open the submit dialog with only that shot ticked. */
  @Output() readonly newVersion = new EventEmitter<string>();
  /** After an action, so the parent list refreshes too. */
  @Output() readonly changed = new EventEmitter<void>();

  detail: SequenceSubmission | null = null;
  busy = false;

  ngOnInit(): void { this.load(); }

  /** Fetch now, then every 10 s until the version is final (poll() also ends on 401 or 403). */
  load(): void {
    const failed = (): void => {
      this.snackBar.open('Failed to load the shots', 'Dismiss', { duration: 5000 });
    };
    this.sub?.unsubscribe();
    this.sub = this.sequences.pollDetail(this.submissionId, failed).pipe(takeWhile(isActive, true))
      .subscribe({ next: d => { this.detail = d; }, error: failed });
  }

  ngOnDestroy(): void { this.sub?.unsubscribe(); }

  problems(shot: SequenceShot): string[] {
    return [shot.error_message, shot.video_error, shot.sound_error, shot.sound_video_error].filter(t => !!t);
  }

  rerender(shot: SequenceShot): void {
    this.busy = true;
    this.sequences.rerenderShot(this.submissionId, shot.index).subscribe({
      next: () => {
        this.busy = false;
        this.snackBar.open('Shot queued again', 'Dismiss', { duration: 3000 });
        this.load();
        this.changed.emit();
      },
      error: (error: unknown) => {
        this.busy = false;
        this.snackBar.open(sequenceErrorText(error, 'Failed to render the shot again'), 'Dismiss', { duration: 5000 });
      },
    });
  }
}
