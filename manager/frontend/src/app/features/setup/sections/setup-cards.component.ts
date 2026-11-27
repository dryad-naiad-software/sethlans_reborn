// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, Input } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatRadioModule } from '@angular/material/radio';
import { SetupFieldErrorsComponent } from '../setup-field-errors.component';
import { errorsFor } from '../setup-errors';
import { FieldErrors, SetupCard, SetupForm } from '../setup.types';

/**
 * Compute method and cards (`worker-card-selection.md` O1, O10): one
 * checkbox per card, none ticked at first, GPU with no card refused,
 * Combine GPUs with two or more ticked. A ticked card that drives a screen
 * gets a warning; the person decides.
 *
 * A saved card this machine no longer has (removed or swapped) is never
 * dropped quietly: it stays ticked with "not found on this machine" and a
 * warning, so the person unticks it. Counts and warnings use only the
 * cards that are here.
 */
@Component({
  selector: 'app-setup-cards',
  standalone: true,
  imports: [FormsModule, MatCheckboxModule, MatRadioModule, SetupFieldErrorsComponent],
  template: `
    @let worker = form.worker;
    @if (worker.compute_env_override) {
      <p class="setup-note">
        SETHLANS_COMPUTE_* environment variables set this worker's compute method and
        cards, so the choice below is not saved.
      </p>
    }
    <div class="setup-block">Render with</div>
    <mat-radio-group class="setup-block" [(ngModel)]="worker.compute"
                     [disabled]="worker.compute_env_override" aria-label="Render with">
      <mat-radio-button value="cpu">CPU</mat-radio-button>
      <mat-radio-button value="gpu">GPU</mat-radio-button>
      <mat-radio-button value="cpu_gpu">CPU and GPU</mat-radio-button>
    </mat-radio-group>
    <app-setup-field-errors [messages]="err('worker.compute')" />

    <div class="setup-block">Graphics cards to render on</div>
    @if (form.cards_os_probe_failed) {
      <p class="setup-warning">The operating system's list of graphics cards could not be read.</p>
    }
    @if (missingCards().length) {
      <p class="setup-warning">
        A card chosen earlier was not found on this machine; untick it to continue.
      </p>
    }
    @for (address of missingCards(); track address) {
      <mat-checkbox class="setup-block missing-card" [checked]="true"
                    (change)="setAddress(address, $event.checked)">
        <span class="setup-mono">{{ address }}</span> — not found on this machine
      </mat-checkbox>
    }
    @for (card of form.cards; track card.address) {
      <mat-checkbox class="setup-block" [checked]="isTicked(card)"
                    [disabled]="worker.compute_env_override"
                    (change)="setTicked(card, $event.checked)">
        {{ card.name || 'Unnamed card' }}{{ card.brand ? ' (' + card.brand + ')' : '' }}
        <span class="setup-note">
          {{ card.address }}{{ card.primary_screen ? ', main screen'
            : card.screen ? ', drives a screen' : '' }}
        </span>
      </mat-checkbox>
    } @empty {
      <p class="setup-note">No graphics cards were found.</p>
    }
    @if (worker.compute !== 'cpu' && tickedCards().length === 0 && !err('worker.cards').length) {
      <p class="setup-warning">GPU rendering needs at least one ticked graphics card.</p>
    }
    @if (screenCard(); as card) {
      <p class="setup-warning">
        {{ card.name }} drives {{ card.primary_screen ? 'the main screen' : 'a screen' }};
        rendering on it can make the desktop slow.
      </p>
    }
    @if (brands().length > 1 && !err('worker.cards').length) {
      <p class="setup-warning">Cards of one brand only can be ticked together.</p>
    }
    <app-setup-field-errors [messages]="err('worker.cards')" />
    @if (tickedCards().length >= 2) {
      <mat-checkbox class="setup-block" [(ngModel)]="worker.combine"
                    [disabled]="worker.compute_env_override">
        Combine GPUs (one job uses all ticked cards)
      </mat-checkbox>
    }
    <app-setup-field-errors [messages]="err('worker.combine')" />
  `,
  styles: [`mat-radio-button { margin-right: 16px; }`],
})
export class SetupCardsComponent {
  @Input({ required: true }) form!: SetupForm;
  @Input() errors: FieldErrors = {};

  err(key: string): string[] {
    return errorsFor(this.errors, key);
  }

  isTicked(card: SetupCard): boolean {
    return this.form.worker.cards.includes(card.address);
  }

  setTicked(card: SetupCard, ticked: boolean): void {
    this.setAddress(card.address, ticked);
  }

  /** Ticked addresses that are not among this machine's cards, in saved order. */
  missingCards(): string[] {
    const here = new Set(this.form.cards.map(card => card.address));
    return this.form.worker.cards.filter(address => !here.has(address));
  }

  /** The ticked cards that are on this machine. */
  tickedCards(): SetupCard[] {
    return this.form.cards.filter(card => this.isTicked(card));
  }

  /** Ticks or unticks an address; an unticked missing card leaves the list. */
  setAddress(address: string, ticked: boolean): void {
    const worker = this.form.worker;
    const rest = worker.cards.filter(entry => entry !== address);
    worker.cards = ticked ? [...rest, address] : rest;
    if (this.tickedCards().length < 2) {
      worker.combine = false;
    }
  }

  /** The first ticked card that drives a screen, the main one first. */
  screenCard(): SetupCard | null {
    const ticked = this.tickedCards();
    return ticked.find(card => card.primary_screen) ?? ticked.find(card => card.screen) ?? null;
  }

  brands(): string[] {
    return [...new Set(this.tickedCards().map(card => card.brand).filter(brand => brand !== null))];
  }
}
