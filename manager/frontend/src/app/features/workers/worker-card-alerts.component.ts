// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import {
  Component, DestroyRef, ElementRef, EventEmitter, Injector, Input, Output, afterNextRender, inject,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { HttpErrorResponse } from '@angular/common/http';
import { DatePipe } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { finalize } from 'rxjs';
import { WorkerService } from '../../core/services/worker.service';
import { CardAlert, DismissCardAlertResponse } from '../../core/services/worker-cards.model';
import { actionErrorText, alertLabel, cardName, validTime } from './worker-cards.util';

/**
 * A worker's open card alerts with their Dismiss buttons (FR-CF-4).
 * Every alert text is shown through interpolation only.
 */
@Component({
  selector: 'app-worker-card-alerts',
  standalone: true,
  imports: [DatePipe, MatButtonModule, MatIconModule],
  template: `
    @if (alerts.length > 0) {
      <ul class="alerts" tabindex="-1">
        @for (alert of alerts; track alert.id) {
          <li class="alert">
            <mat-icon class="alert-icon" aria-hidden="true">error</mat-icon>
            <span class="alert-text">
              <strong class="alert-kind">{{ label(alert) }}</strong>:
              <span class="alert-card">{{ name(alert) }}</span>
              @if (alert.detail) {
                <span class="alert-detail"> ({{ alert.detail }})</span>
              }
              @if (time(alert); as raised) {
                <span class="alert-time"> since {{ raised | date:'medium' }}</span>
              }
            </span>
            @if (isDismissed(alert)) {
              <span class="dismissal-sent" role="status">Dismissal sent</span>
            } @else {
              <button mat-stroked-button type="button" class="dismiss"
                      [attr.data-alert-index]="$index" [disabled]="inFlight.has(alert.id)" (click)="dismiss(alert)">
                Dismiss
              </button>
            }
          </li>
        }
      </ul>
      @if (error) {
        <p class="action-error" role="alert">{{ error }}</p>
      }
    }
  `,
  styles: [`
    .alerts { list-style: none; margin: 0 0 8px; padding: 0; }
    .alert {
      display: flex; align-items: center; gap: 8px; padding: 4px 0;
      color: #b71c1c;
    }
    .alert-icon { flex: none; }
    .alert-text { flex: 1; }
    .dismissal-sent { color: #666; font-style: italic; }
    .action-error { color: #b71c1c; margin: 0 0 8px; }
  `],
})
export class WorkerCardAlertsComponent {
  @Input({ required: true }) workerId!: number;
  @Input() alerts: CardAlert[] = [];
  @Input() dismissals: string[] = [];
  /** The 200 body of a dismissal, applied to the worker row at once. */
  @Output() dismissed = new EventEmitter<DismissCardAlertResponse>();

  private readonly workerService = inject(WorkerService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);

  /** Alert ids whose dismissal request is in flight. */
  readonly inFlight = new Set<string>();
  error: string | null = null;

  label(alert: CardAlert): string { return alertLabel(alert.kind); }

  name(alert: CardAlert): string { return cardName(alert); }

  time(alert: CardAlert): string | null { return validTime(alert.raised_at); }

  isDismissed(alert: CardAlert): boolean {
    return Array.isArray(this.dismissals) && this.dismissals.includes(alert.id);
  }

  dismiss(alert: CardAlert): void {
    const id = alert.id;
    const index = this.alerts.indexOf(alert);
    if (this.inFlight.has(id)) return;
    this.inFlight.add(id);
    this.error = null;
    this.workerService.dismissCardAlert(this.workerId, id).pipe(
      finalize(() => this.inFlight.delete(id)),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe({
      next: (body) => {
        this.dismissed.emit(body);
        afterNextRender(() => this.moveFocus(index), { injector: this.injector });
      },
      error: (err: HttpErrorResponse) => { this.error = this.errorText(alert, err); },
    });
  }

  /** The error sentence; with several alerts listed it names the card (C-26e). */
  private errorText(alert: CardAlert, err: HttpErrorResponse): string {
    const text = actionErrorText(err.status, err.error);
    return this.alerts.length > 1 ? `Dismissing the alert for ${cardName(alert)} failed: ${text}` : text;
  }

  /** After a dismissal the button is gone: focus the next Dismiss button, else the list. */
  private moveFocus(index: number): void {
    const root = this.host.nativeElement;
    const buttons = Array.from(root.querySelectorAll<HTMLButtonElement>('button.dismiss'))
      .filter(b => !b.disabled);
    const next = buttons.find(b => Number(b.dataset['alertIndex']) > index) ?? buttons[0];
    (next ?? root.querySelector<HTMLElement>('ul.alerts'))?.focus();
  }
}
