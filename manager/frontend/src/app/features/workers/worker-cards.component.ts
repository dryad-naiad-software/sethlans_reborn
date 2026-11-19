// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, EventEmitter, Input, Output } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { Worker } from '../../core/services/worker.service';
import {
  CardAlert, CardEntry, CardNotice, CardStatus, DismissCardAlertResponse,
} from '../../core/services/worker-cards.model';
import { WorkerCardAlertsComponent } from './worker-card-alerts.component';
import {
  addressLabel, alertsOf, cardName, cardsOf, hasCardStatus, noticeText, stateLabel, stateTone,
} from './worker-cards.util';

/**
 * The read-only graphics card panel of one worker, shown in its expanded
 * row (FR-CF-3). Every text from the worker goes in through interpolation;
 * the only link is the worker UI address the list already validated.
 */
@Component({
  selector: 'app-worker-cards',
  standalone: true,
  imports: [MatButtonModule, MatIconModule, WorkerCardAlertsComponent],
  template: `
    <section class="cards-panel" aria-label="Graphics cards">
      @if (status; as s) {
        @if (s.problem) {
          <p class="problem">{{ s.problem }}</p>
        }
        @if (s.rebuild_waiting) {
          <p class="rebuild-waiting">
            Waiting for running GPU jobs to finish before using the new card list.
          </p>
        }
        <app-worker-card-alerts
          [workerId]="worker.id"
          [alerts]="alerts"
          [dismissals]="worker.card_alert_dismissals"
          (dismissed)="cardsUpdated.emit($event)" />
        @for (warning of s.warnings ?? []; track $index) {
          <p class="warning">
            <mat-icon aria-hidden="true">warning</mat-icon>
            <span>{{ warning.text }}</span>
          </p>
        }
        @if (cards.length > 0) {
          <table class="cards">
            <thead>
              <tr>
                <th>Card</th><th>Address</th><th>Ticked</th><th>State</th><th>Note</th>
              </tr>
            </thead>
            <tbody>
              @for (card of cards; track card.key) {
                <tr class="card">
                  <td class="card-name">{{ name(card) }}</td>
                  <td class="card-address">{{ address(card) }}</td>
                  <td class="card-ticked">{{ card.ticked ? 'Yes' : 'No' }}</td>
                  <td class="card-state" [class]="'tone-' + tone(card)">
                    {{ state(card) }}
                  </td>
                  <td class="card-note">{{ card.note }}</td>
                </tr>
              }
            </tbody>
          </table>
        } @else {
          <p class="no-cards">This worker reported no graphics cards.</p>
        }
        @for (notice of s.notices ?? []; track $index) {
          <p class="notice">{{ noticeLine(notice) }}</p>
        }
      } @else {
        <p class="no-status">This worker has not reported its graphics cards yet.</p>
      }
      @if (uiUrl) {
        <a mat-stroked-button class="worker-ui-link" [href]="uiUrl"
           target="_blank" rel="noopener noreferrer">
          <mat-icon>open_in_new</mat-icon>
          Open the worker's page
        </a>
      }
    </section>
  `,
  styles: [`
    .cards-panel { padding: 8px 16px 16px; }
    .problem { color: #b71c1c; font-weight: 500; }
    .rebuild-waiting { color: #555; font-style: italic; }
    .warning {
      display: flex; align-items: center; gap: 8px;
      color: #8a5300; background: #fff4e0; padding: 4px 8px; border-radius: 4px;
    }
    .notice { color: #0d47a1; }
    .cards { border-collapse: collapse; margin: 8px 0; }
    .cards th, .cards td { text-align: left; padding: 4px 12px 4px 0; }
    .tone-good { color: #1b5e20; }
    .tone-bad { color: #b71c1c; }
    .tone-amber { color: #8a5300; }
    .card-note { color: #555; }
    .no-status, .no-cards { color: #666; }
  `],
})
export class WorkerCardsComponent {
  @Input({ required: true }) worker!: Worker;
  /** The worker UI address, already limited to http(s) by the list; never built from card text. */
  @Input() uiUrl: string | null = null;
  /** A card action's body (today the Dismiss 200 body), for the list to apply to the row. */
  @Output() cardsUpdated = new EventEmitter<DismissCardAlertResponse>();

  /** The report when it is usable (`schema === 1`), else `null` (`null` and `{}` alike). */
  get status(): CardStatus | null {
    return hasCardStatus(this.worker.card_status) ? this.worker.card_status : null;
  }

  get cards(): CardEntry[] { return cardsOf(this.worker.card_status); }

  get alerts(): CardAlert[] { return alertsOf(this.worker.card_status); }

  name(card: CardEntry): string { return cardName(card); }

  address(card: CardEntry): string { return addressLabel(card.key); }

  state(card: CardEntry): string { return stateLabel(card.state); }

  tone(card: CardEntry): string { return stateTone(card.state); }

  noticeLine(notice: CardNotice): string { return noticeText(notice); }
}
