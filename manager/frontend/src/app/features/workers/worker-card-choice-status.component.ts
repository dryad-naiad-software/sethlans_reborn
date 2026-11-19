// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import {
  Component, DestroyRef, ElementRef, EventEmitter, Injector, Input, LOCALE_ID, OnChanges, Output,
  afterNextRender, inject,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { HttpErrorResponse } from '@angular/common/http';
import { formatDate } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { finalize } from 'rxjs';
import { WorkerService, Worker } from '../../core/services/worker.service';
import { CardChoiceOutcome, CardChoiceRequest, CardChoiceSendResponse } from '../../core/services/worker-cards.model';
import { actionErrorText, validTime } from './worker-cards.util';
import {
  WithdrawMark, ageText, hasOutcome, hasPending, outcomeLine, readOnlyReason, withdrawnEnds,
} from './worker-card-choice.util';
import { WorkerCardChoiceFormComponent } from './worker-card-choice-form.component';
import { WorkerCardPatch } from './worker-row.util';

export const WITHDRAWN_TEXT =
  'Withdrawn. If the worker had already received it, its answer will still appear here.';

/** A worker's card choice section (FR-CF-6, FR-CF-7): pending change, last outcome, Change or a read-only sentence. */
@Component({
  selector: 'app-worker-card-choice-status',
  standalone: true,
  imports: [MatButtonModule, WorkerCardChoiceFormComponent],
  template: `
    <section class="choice-status" aria-label="Graphics card changes" tabindex="-1">
      @if (pending; as p) {
        <p class="pending" tabindex="-1">Change sent {{ age(p) }} ago, waiting for the worker.</p>
        @if (worker.status === 'OFFLINE') {
          <p class="offline">The worker is offline; the change waits for its next heartbeat.</p>
        }
        <button mat-stroked-button type="button" class="withdraw" [disabled]="withdrawing" (click)="withdraw()">
          Withdraw
        </button>
      } @else if (withdrawn) {
        <p class="withdrawn" role="status" tabindex="-1">{{ withdrawnText }}</p>
      }
      @if (outcome; as o) {
        <p class="outcome" [class.refused]="o.result === 'refused'">{{ outcomeText(o) }}</p>
        @if (o.requested_by) {
          <p class="requested">Requested by {{ o.requested_by }}{{ at(o.requested_at) }}.</p>
        }
      }
      @if (error) {
        <p class="action-error" role="alert">{{ error }}</p>
      }
      @if (reason) {
        <p class="read-only">{{ reason }}</p>
      } @else if (formOpen) {
        <app-worker-card-choice-form [worker]="worker" (sent)="onSent($event)" (cancelled)="cancel()" />
      } @else {
        <button mat-stroked-button type="button" class="change" (click)="open()">Change</button>
      }
    </section>
  `,
  styles: [`
    .choice-status { margin-top: 8px; } .choice-status p { margin: 4px 0; }
    .pending { color: #0d47a1; } .outcome { color: #1b5e20; }
    .offline, .withdrawn, .requested, .read-only { color: #555; }
    .outcome.refused, .action-error { color: #b71c1c; }
  `],
})
export class WorkerCardChoiceStatusComponent implements OnChanges {
  @Input({ required: true }) worker!: Worker;
  /** Row fields to replace at once after Send (202) or Withdraw (204). */
  @Output() choiceUpdated = new EventEmitter<WorkerCardPatch>();

  private readonly workerService = inject(WorkerService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly locale = inject(LOCALE_ID);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);
  /** Set by a Withdraw; cleared when another request or a new outcome arrives. */
  private withdrawMark: WithdrawMark | null = null;

  readonly withdrawnText = WITHDRAWN_TEXT;
  formOpen = false;
  withdrawing = false;
  error: string | null = null;
  /** The time the ages are measured against; refreshed with every new row (each poll). */
  now = Date.now();

  ngOnChanges(): void {
    this.now = Date.now();
    if (this.withdrawMark && withdrawnEnds(this.withdrawMark, this.worker)) this.withdrawMark = null;
  }

  get withdrawn(): boolean { return this.withdrawMark !== null; }

  get reason(): string | null { return readOnlyReason(this.worker.card_status); }

  get pending(): CardChoiceRequest | null {
    const r = this.worker.card_choice_request; // a late poll may still carry the withdrawn one
    return hasPending(r) && r.id !== this.withdrawMark?.requestId ? r : null;
  }

  get outcome(): CardChoiceOutcome | null {
    return hasOutcome(this.worker.card_choice_outcome) ? this.worker.card_choice_outcome : null;
  }

  age(request: CardChoiceRequest): string { return ageText(request.requested_at, this.now); }

  at(value: string | null | undefined): string {
    const time = validTime(value);
    return time ? ` at ${formatDate(time, 'medium', this.locale)}` : '';
  }

  outcomeText(outcome: CardChoiceOutcome): string { return outcomeLine(outcome, this.at(outcome.finished_at)); }

  open(): void {
    this.formOpen = true;
    this.error = null;
    this.focusAfterRender('app-worker-card-choice-form select.method-select');
  }

  cancel(): void {
    this.formOpen = false;
    this.focusAfterRender('button.change');
  }

  onSent(answer: CardChoiceSendResponse): void {
    this.formOpen = false;
    this.withdrawMark = null;
    this.error = null;
    this.choiceUpdated.emit({ card_choice_request: answer.card_choice_request });
    this.focusAfterRender('p.pending', 'button.withdraw', 'button.change');
  }

  withdraw(): void {
    if (this.withdrawing) return;
    this.withdrawing = true;
    this.error = null;
    this.workerService.withdrawCardChoice(this.worker.id).pipe(
      finalize(() => { this.withdrawing = false; }),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe({
      next: () => {
        this.withdrawMark = { requestId: this.pending?.id ?? '', outcomeId: this.outcome?.id ?? null };
        this.choiceUpdated.emit({ card_choice_request: null });
        this.focusAfterRender('p.withdrawn', 'button.change');
      },
      error: (err: HttpErrorResponse) => { this.error = actionErrorText(err.status, err.error); },
    });
  }

  /** The button that was used is gone after the next render: focus the first match, else the section. */
  private focusAfterRender(...selectors: string[]): void {
    afterNextRender(() => {
      const root = this.host.nativeElement;
      const target = selectors.map(s => root.querySelector<HTMLElement>(s)).find(e => !!e);
      (target ?? root.querySelector<HTMLElement>('section.choice-status'))?.focus();
    }, { injector: this.injector });
  }
}
