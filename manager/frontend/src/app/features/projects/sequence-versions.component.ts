// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, EventEmitter, Input, OnDestroy, OnInit, Output, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatSnackBar } from '@angular/material/snack-bar';
import { Observable, Subscription, filter, takeWhile } from 'rxjs';
import { SequenceService, sequenceErrorText } from '../../core/services/sequence.service';
import { SEQUENCE_STATUS_LABELS, SequenceSubmission } from '../../core/services/sequence.types';
import { canMergeSound, canRetrySound, canRetryVideo, isActive, needsShots } from './sequence-actions.util';
import { ConfirmDialogComponent, ConfirmDialogData } from '../../shared/confirm-dialog.component';
import { SequenceDownloadsComponent } from './sequence-downloads.component';
import { SequenceVersionShotsComponent } from './sequence-version-shots.component';

/** What "New version of this shot" asks the project page to open. */
export interface SequenceShotRequest {
  version: SequenceSubmission;
  shot: string;
}

/**
 * FR-SU-3: the project's sequence versions, polled every 10 s while one is
 * active (restarted by an action or a submit): name,
 * status (with "Partly done"), shots done/total, routing note, errors and
 * warnings, downloads of the whole version, and the version actions.
 * Project and farm text is interpolated only (FR-SU-4b).
 */
@Component({
  selector: 'app-sequence-versions',
  standalone: true,
  imports: [MatButtonModule, SequenceDownloadsComponent, SequenceVersionShotsComponent],
  template: `
    <h2>Sequence versions</h2>
    @if (versions.length === 0) {
      <p class="empty">No sequence versions yet.</p>
    }
    @for (v of versions; track v.id) {
      <div class="version">
        <div class="line">
          <strong class="version-name">{{ v.name }}</strong>
          <span class="version-status">{{ label(v) }}</span>
          <span class="version-shots">Shots {{ v.shots_done }}/{{ v.shots_total }}
            @if (v.shots_error) { ({{ v.shots_error }} failed) }</span>
          <button mat-button class="toggle-shots" (click)="toggle(v.id)">
            {{ expanded.has(v.id) ? 'Hide shots' : 'Show shots' }}</button>
        </div>
        @if (v.routing_note) { <div class="routing-note">{{ v.routing_note }}</div> }
        @for (problem of problems(v); track $index) { <div class="version-error">{{ problem }}</div> }
        @for (warning of v.warnings; track $index) { <div class="version-warning">{{ warning }}</div> }
        <div class="line">
          <app-sequence-downloads [submissionId]="v.id" [downloads]="v.downloads" />
          @if (!isFinal(v)) {
            <button mat-button class="cancel" (click)="cancel(v)">Cancel</button>
          }
          @if (canRetrySound(v, details.get(v.id)?.shots)) {
            <button mat-button class="retry-sound" (click)="act(sequences.retrySound(v.id), 'Sound queued again')">Retry sound</button>
          }
          @if (canRetryVideo(v, details.get(v.id)?.shots)) {
            <button mat-button class="retry-video" (click)="act(sequences.retryVideo(v.id), 'Video build queued again')">Retry video</button>
          }
          @if (canMergeSound(v)) {
            <button mat-button class="merge-sound" (click)="act(sequences.mergeSound(v.id), 'Merging sound')">Merge sound</button>
          }
          <button mat-button color="warn" class="delete" (click)="remove(v)">Delete</button>
        </div>
        @if (expanded.has(v.id)) {
          <app-sequence-version-shots [submissionId]="v.id" (changed)="refresh()"
            (newVersion)="newShotVersion.emit({ version: v, shot: $event })" />
        }
      </div>
    }
  `,
  styles: [`.version { border-top: 1px solid rgba(0,0,0,0.12); padding: 8px 0; }
    .line { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    .version-error { color: #b00020; } .routing-note, .version-warning { color: rgba(0,0,0,0.6); }`],
})
export class SequenceVersionsComponent implements OnInit, OnDestroy {
  readonly sequences = inject(SequenceService);
  private readonly snackBar = inject(MatSnackBar);
  private readonly dialog = inject(MatDialog);
  private sub?: Subscription;
  private readonly detailSubs = new Subscription();
  readonly canRetrySound = canRetrySound;
  readonly canRetryVideo = canRetryVideo;
  readonly canMergeSound = canMergeSound;

  @Input({ required: true }) projectId!: string;
  @Output() readonly newShotVersion = new EventEmitter<SequenceShotRequest>();

  versions: SequenceSubmission[] = [];
  expanded = new Set<number>();
  /** Each version's detail (its shots' items) for the retry buttons, kept per status. */
  readonly details = new Map<number, SequenceSubmission>();

  ngOnInit(): void { this.refresh(); }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
    this.detailSubs.unsubscribe();
  }

  /**
   * (Re)start polling: fetch now, then every 10 s while a version is active;
   * stop after a fetch where all are final. poll() also ends on 401 or 403.
   */
  refresh(): void {
    const failed = (): void => {
      this.snackBar.open('Failed to load sequence versions', 'Dismiss', { duration: 5000 });
    };
    this.sub?.unsubscribe();
    this.details.clear();
    this.sub = this.sequences.pollList(this.projectId, failed).pipe(
      takeWhile(rows => rows.some(isActive), true),
    ).subscribe({ next: rows => this.show(rows), error: failed });
  }

  label(v: SequenceSubmission): string {
    const text = SEQUENCE_STATUS_LABELS[v.status] ?? v.status;
    return v.cancel_requested && !this.isFinal(v) ? `${text} (canceling)` : text;
  }

  isFinal(v: SequenceSubmission): boolean { return !isActive(v); }

  problems(v: SequenceSubmission): string[] {
    return [v.error_message, v.video_error, v.sound_error, v.sound_video_error].filter(t => !!t);
  }

  toggle(id: number): void {
    if (this.expanded.has(id)) this.expanded.delete(id); else this.expanded.add(id);
  }

  cancel(v: SequenceSubmission): void {
    this.confirmed({ title: 'Cancel version', message: `Cancel ${v.name}?`, confirmText: 'Cancel version' })
      .subscribe(() => this.act(this.sequences.cancel(v.id), 'Version canceled'));
  }

  remove(v: SequenceSubmission): void {
    const message = `Delete ${v.name} with its frames, videos and sound?`;
    this.confirmed({ title: 'Delete version', message, confirmText: 'Delete' }).subscribe(() => {
      this.act(this.sequences.delete(v.id), 'Version deleted', () => this.expanded.delete(v.id));
    });
  }

  act(call: Observable<unknown>, done: string, after?: () => void): void {
    call.subscribe({
      next: () => {
        after?.();
        this.snackBar.open(done, 'Dismiss', { duration: 3000 });
        this.refresh();
      },
      error: (error: unknown) => {
        this.snackBar.open(sequenceErrorText(error, 'The action failed'), 'Dismiss', { duration: 5000 });
      },
    });
  }

  /** Keep the rows; load a version's detail once per status when its retry buttons need the shots. */
  private show(rows: SequenceSubmission[]): void {
    this.versions = rows;
    for (const v of rows) {
      const known = this.details.get(v.id);
      if (!needsShots(v) || (known && known.status === v.status)) continue;
      this.details.set(v.id, { ...v, shots: known?.shots });
      this.detailSubs.add(this.sequences.get(v.id).subscribe({
        next: d => this.details.set(v.id, d), error: () => this.details.delete(v.id),
      }));
    }
  }

  private confirmed(data: ConfirmDialogData): Observable<boolean> {
    return this.dialog.open(ConfirmDialogComponent, { data }).afterClosed()
      .pipe(filter((ok: boolean) => ok === true));
  }
}
