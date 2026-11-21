// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, EventEmitter, Input, OnDestroy, Output, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatSnackBar } from '@angular/material/snack-bar';
import { Subscription, switchMap, takeWhile } from 'rxjs';
import { SequenceService, sequenceErrorText } from '../../core/services/sequence.service';
import { DiscoveryKey, SequenceDiscovery } from '../../core/services/sequence.types';

/**
 * "Read shots" (FR-SU-2): starts or reuses a discovery, polls it every 3 s
 * until it is DONE or ERROR, then lists its shots with checkboxes (all
 * ticked, or only `preselect` when given). Shot names and warnings come
 * from the project, so they are shown by interpolation only (FR-SU-4b).
 */
@Component({
  selector: 'app-sequence-shots-picker',
  standalone: true,
  imports: [MatButtonModule, MatCheckboxModule],
  template: `
    <div class="picker">
      <button mat-stroked-button type="button" class="read-shots" (click)="read()"
              [disabled]="!key || reading">Read shots</button>
      @if (discovery) {
        <span class="discovery-status">Shots: {{ discovery.status }}</span>
      }
    </div>
    @if (discovery?.error_message) {
      <p class="discovery-error">{{ discovery?.error_message }}</p>
    }
    @for (warning of discovery?.warnings ?? []; track $index) {
      <p class="discovery-warning">{{ warning }}</p>
    }
    @if (discovery?.status === 'DONE') {
      <p class="playback">Frames {{ discovery?.playback_start }} to {{ lastFrame() }}</p>
      <ul class="shots">
        @for (shot of discovery?.shots ?? []; track shot.index) {
          <li>
            <mat-checkbox [checked]="ticked.has(shot.name)" (change)="toggle(shot.name, $event.checked)">
              {{ shot.name }} ({{ shot.start }}-{{ shot.end - 1 }})
            </mat-checkbox>
          </li>
        }
      </ul>
    }
  `,
  styles: [`
    .picker { display: flex; gap: 12px; align-items: center; }
    .shots { list-style: none; padding: 0; margin: 4px 0; max-height: 200px; overflow: auto; }
    .discovery-error { color: #b00020; }
  `],
})
export class SequenceShotsPickerComponent implements OnDestroy {
  private readonly sequences = inject(SequenceService);
  private readonly snackBar = inject(MatSnackBar);
  private sub?: Subscription;

  /** What to read; null while the form's map, sequence or preset is missing. */
  @Input() key: DiscoveryKey | null = null;
  /** Names to tick when the shots arrive; null ticks all. */
  @Input() preselect: string[] | null = null;
  @Output() readonly discoveryChange = new EventEmitter<SequenceDiscovery | null>();
  @Output() readonly selectedChange = new EventEmitter<string[]>();

  discovery: SequenceDiscovery | null = null;
  ticked = new Set<string>();
  reading = false;

  ngOnDestroy(): void { this.sub?.unsubscribe(); }

  lastFrame(): number | null {
    const end = this.discovery?.playback_end;
    return end === null || end === undefined ? null : end - 1;
  }

  /** Forget the shown discovery (the form's key changed). */
  reset(): void {
    this.sub?.unsubscribe();
    this.reading = false;
    if (this.discovery) {
      this.discovery = null;
      this.ticked = new Set();
      this.discoveryChange.emit(null);
      this.selectedChange.emit([]);
    }
  }

  read(): void {
    if (!this.key) return;
    this.reset();
    this.reading = true;
    const failed = (error: unknown): void => {
      this.snackBar.open(sequenceErrorText(error, 'Failed to read shots'), 'Dismiss', { duration: 5000 });
    };
    this.sub = this.sequences.discover(this.key).pipe(
      switchMap(first => this.sequences.pollDiscovery(first.id, failed)),
      takeWhile(d => d.status !== 'DONE' && d.status !== 'ERROR', true),
    ).subscribe({
      next: d => this.show(d),
      error: (error: unknown) => { this.reading = false; failed(error); },
      complete: () => { this.reading = false; },
    });
  }

  toggle(name: string, checked: boolean): void {
    if (checked) this.ticked.add(name); else this.ticked.delete(name);
    this.emitSelected();
  }

  private show(discovery: SequenceDiscovery): void {
    this.discovery = discovery;
    if (discovery.status === 'DONE') {
      const names = (discovery.shots ?? []).map(s => s.name);
      const wanted = this.preselect;
      this.ticked = new Set(wanted ? names.filter(n => wanted.includes(n)) : names);
      this.emitSelected();
    }
    this.discoveryChange.emit(discovery);
  }

  private emitSelected(): void {
    const order = (this.discovery?.shots ?? []).map(s => s.name);
    this.selectedChange.emit(order.filter(n => this.ticked.has(n)));
  }
}
