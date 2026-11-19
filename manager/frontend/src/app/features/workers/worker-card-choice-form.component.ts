// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, DestroyRef, EventEmitter, Input, OnInit, Output, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { HttpErrorResponse } from '@angular/common/http';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { finalize } from 'rxjs';
import { WorkerService, Worker } from '../../core/services/worker.service';
import { CardChoiceSendResponse, CardMethod } from '../../core/services/worker-cards.model';
import { actionErrorText, addressLabel, oneFamily, screenLabels } from './worker-cards.util';
import {
  ChoiceSnapshot, METHOD_OPTIONS, SnapshotCard, buildChoiceBody, canNewlyTick, hasPending, includesGpu,
  isMethod, revisionOf, shownCards, takeSnapshot,
} from './worker-card-choice.util';

export const STALE_TEXT = 'The worker\'s choice changed since you opened this form.';

/**
 * The form that sends a graphics card change to a worker (FR-CF-5). It
 * works on the snapshot taken when it opened (R2); polling never resets it
 * (R1). Every card text is shown through interpolation only.
 */
@Component({
  selector: 'app-worker-card-choice-form',
  standalone: true,
  imports: [MatButtonModule, MatCheckboxModule, MatFormFieldModule, MatInputModule],
  template: `
    <section class="choice-form" aria-label="Change the graphics card choice">
      <mat-form-field class="method" subscriptSizing="dynamic">
        <mat-label>Method</mat-label>
        <select matNativeControl #methodSelect class="method-select" (change)="setMethod(methodSelect.value)">
          @for (option of methods; track option.value) {
            <option [value]="option.value" [selected]="option.value === method">{{ option.label }}</option>
          }
        </select>
      </mat-form-field>

      <ul class="choice-cards">
        @for (card of cards; track card.key) {
          <li class="choice-card">
            <!-- Two branches: mat-checkbox's aria-describedby input takes no null. -->
            @if (canTick(card)) {
              <mat-checkbox class="card-tick" [checked]="ticked.has(card.key)" (change)="tick(card.key, $event.checked)">
                {{ card.name }}
                @if (address(card); as a) {<span class="card-address"> ({{ a }})</span>}
              </mat-checkbox>
            } @else {
              <mat-checkbox class="card-tick" [checked]="ticked.has(card.key)" disabled [aria-describedby]="heldId($index)">
                {{ card.name }}
                @if (address(card); as a) {<span class="card-address"> ({{ a }})</span>}
              </mat-checkbox>
            }
            @for (label of marks(card); track label) {
              <span class="screen-mark">{{ label }}</span>
            }
            @if (!canTick(card)) {
              <span class="held-note" [id]="heldId($index)">Dismiss its alert first</span>
            }
          </li>
        } @empty {
          <li class="no-choice-cards">No graphics card can be ticked.</li>
        }
      </ul>

      @if (combineShown) {
        <mat-checkbox class="combine" [checked]="combine" (change)="combine = $event.checked">
          Combine GPUs
        </mat-checkbox>
      }
      @if (missingTicked) {
        <p class="missing-note">Missing cards are not listed; sending this change unticks them.</p>
      }
      <p class="help">
        Unreal renders on a ticked card when it is switched on in the worker's settings.
        Blender uses the ticked cards only when the method includes GPU.
      </p>
      @if (pendingNow) {
        <p class="replaces-note">Sending replaces the change that is waiting.</p>
      }
      @if (needsTick) {
        <p class="needs-tick">Tick at least one card to render on the GPU.</p>
      }
      @if (stale) {
        <p class="stale" role="alert">{{ staleText }}</p>
      }
      @if (error) {
        <p class="action-error" role="alert">{{ error }}</p>
      }
      <div class="actions">
        <button mat-flat-button type="button" class="send" [disabled]="sendDisabled" (click)="send()">
          Send
        </button>
        @if (stale) {
          <button mat-stroked-button type="button" class="start-again" (click)="startAgain()">
            Start again
          </button>
        }
        <button mat-button type="button" class="cancel" [disabled]="sending" (click)="cancelled.emit()">
          Cancel
        </button>
      </div>
    </section>
  `,
  styles: [`
    .choice-form { border-top: 1px solid #e0e0e0; margin-top: 8px; padding-top: 8px; }
    .method { width: 220px; }
    .choice-cards { list-style: none; margin: 8px 0; padding: 0; }
    .choice-card { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
    .card-address, .screen-mark { color: #555; }
    .held-note { color: #b71c1c; }
    .help, .missing-note, .needs-tick, .replaces-note { color: #555; margin: 4px 0; }
    .stale, .action-error { color: #b71c1c; margin: 4px 0; }
    .actions { display: flex; gap: 8px; margin-top: 8px; }
  `],
})
export class WorkerCardChoiceFormComponent implements OnInit {
  /** The live worker row; read only for the revision check, never to reset the form. */
  @Input({ required: true }) worker!: Worker;
  /** The 202 body of a successful Send. */
  @Output() sent = new EventEmitter<CardChoiceSendResponse>();
  @Output() cancelled = new EventEmitter<void>();

  private readonly workerService = inject(WorkerService);
  private readonly destroyRef = inject(DestroyRef);

  readonly methods = METHOD_OPTIONS;
  readonly staleText = STALE_TEXT;
  snapshot!: ChoiceSnapshot;
  cards: SnapshotCard[] = [];
  method: CardMethod = 'cpu';
  combine = false;
  ticked = new Set<string>();
  sending = false;
  error: string | null = null;

  ngOnInit(): void { this.startAgain(); }

  /** Takes a new snapshot of the live report and resets the form to it. */
  startAgain(): void {
    this.snapshot = takeSnapshot(this.worker.card_status);
    this.cards = shownCards(this.snapshot);
    this.method = this.snapshot.method;
    this.combine = this.snapshot.gpuMode === 'combined';
    this.ticked = new Set(this.cards.filter(c => c.ticked).map(c => c.key));
    this.error = null;
  }

  /** The live revision differs from the snapshot's. */
  get stale(): boolean { return revisionOf(this.worker.card_status) !== this.snapshot.revision; }

  get needsTick(): boolean { return includesGpu(this.method) && this.ticked.size === 0; }

  /** Only with a GPU method (C-23); `combine` keeps its value while hidden. */
  get combineShown(): boolean {
    return includesGpu(this.method) && oneFamily(this.cards.filter(c => this.ticked.has(c.key)));
  }

  /** A change is waiting now (live row), so a Send replaces it. */
  get pendingNow(): boolean { return hasPending(this.worker.card_choice_request); }

  heldId(index: number): string { return `card-held-${this.worker.id}-${index}`; }

  get missingTicked(): boolean { return this.snapshot.cards.some(c => c.state === 'lost' && c.ticked); }

  get sendDisabled(): boolean { return this.stale || this.sending || this.needsTick; }

  canTick(card: SnapshotCard): boolean { return canNewlyTick(card, this.snapshot.holding); }

  address(card: SnapshotCard): string { return addressLabel(card.key); }

  marks(card: SnapshotCard): string[] { return screenLabels(card); }

  setMethod(value: string): void {
    if (isMethod(value)) this.method = value;
  }

  tick(key: string, checked: boolean): void {
    if (checked) {
      this.ticked.add(key);
    } else {
      this.ticked.delete(key);
    }
  }

  send(): void {
    if (this.sendDisabled) return;
    const body = buildChoiceBody(this.snapshot, this.method, this.combine, this.ticked);
    this.sending = true;
    this.error = null;
    this.workerService.sendCardChoice(this.worker.id, body).pipe(
      finalize(() => { this.sending = false; }),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe({
      next: (answer) => this.sent.emit(answer),
      error: (err: HttpErrorResponse) => { this.error = actionErrorText(err.status, err.error); },
    });
  }
}
