// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, DOCUMENT, OnDestroy, OnInit, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatToolbarModule } from '@angular/material/toolbar';
import { Subscription, catchError, forkJoin, of } from 'rxjs';
import { SetupApiService } from './setup-api.service';
import { SetupFinishComponent } from './setup-finish.component';
import { SetupFormComponent } from './setup-form.component';
import { SetupTokenComponent } from './setup-token.component';
import { FinishInfo, SetupFailure, SetupForm, SetupHealth } from './setup.types';

type Step = 'starting' | 'normal' | 'token' | 'loading' | 'form' | 'finish' | 'broken';

/**
 * The setup page at /setup/ (FR-WZ-10, OQ-8), served by setup mode. It
 * talks to the setup API only (no login, no runtime endpoint): health,
 * then the token, then the form (about 5 s), then the finish page.
 */
@Component({
  selector: 'app-setup-page',
  standalone: true,
  imports: [MatButtonModule, MatProgressSpinnerModule, MatToolbarModule,
    SetupFinishComponent, SetupFormComponent, SetupTokenComponent],
  template: `
    <mat-toolbar color="primary">Sethlans setup</mat-toolbar>
    <main class="setup-page">
      @switch (step) {
        @case ('starting') {
          <mat-spinner diameter="32" />
        }
        @case ('normal') {
          <p>Sethlans is already set up. To change the setup, use Reset setup in the launcher.</p>
          <a mat-raised-button color="primary" href="/login/">Go to the login page</a>
        }
        @case ('token') {
          <app-setup-token [notice]="notice" (authenticated)="loadForm()" />
        }
        @case ('loading') {
          <p class="loading">
            <mat-spinner diameter="24" />
            Reading this machine's settings, graphics cards and the managers on the network
            (about 5 seconds)…
          </p>
        }
        @case ('form') {
          @if (form) {
            <app-setup-form [form]="form" (finished)="finished($event)"
                            (sessionLost)="backToToken($event)" />
          }
        }
        @case ('finish') {
          @if (finish) {
            <app-setup-finish [info]="finish" [bootId]="bootId"
                              (setupAgain)="setupAgain($event)" />
          }
        }
        @case ('broken') {
          <p class="setup-error" role="alert">{{ notice }}</p>
          <button mat-raised-button type="button" (click)="reload()">Try again</button>
        }
      }
    </main>
  `,
  styles: [`
    main { max-width: 720px; margin: 0 auto; padding: 16px; box-sizing: border-box; }
    .loading { display: flex; align-items: center; gap: 12px; }
  `],
})
export class SetupPageComponent implements OnInit, OnDestroy {
  private readonly api = inject(SetupApiService);
  private readonly document = inject(DOCUMENT);

  step: Step = 'starting';
  notice = '';
  bootId = '';
  form: SetupForm | null = null;
  finish: FinishInfo | null = null;
  private subscription?: Subscription;

  ngOnInit(): void {
    this.subscription = this.api.health().subscribe({
      next: health => this.started(health),
      // No health answer: still try the token; its own errors say more.
      error: () => this.backToToken(''),
    });
  }

  ngOnDestroy(): void {
    this.subscription?.unsubscribe();
  }

  loadForm(): void {
    this.step = 'loading';
    this.subscription?.unsubscribe();
    // The health answer is read again with the form: setup mode may have
    // restarted since the page loaded, and the finish page needs its boot id.
    const health = this.api.health().pipe(catchError(() => of(null)));
    this.subscription = forkJoin({ form: this.api.form(), health }).subscribe({
      next: ({ form, health: answer }) => {
        this.bootId = answer?.boot_id ?? this.bootId;
        this.form = form;
        this.step = 'form';
      },
      error: (failure: SetupFailure) => {
        if (failure.status === 403) {
          this.api.clearSession();
          this.backToToken('The setup session ended; enter the setup token again.');
          return;
        }
        this.notice = `The form could not be read: ${failure.message}`;
        this.step = 'broken';
      },
    });
  }

  finished(info: FinishInfo): void {
    this.finish = info;
    this.step = 'finish';
  }

  backToToken(notice: string): void {
    this.form = null;
    this.notice = notice;
    this.step = 'token';
  }

  /** The first start failed and setup mode is back, with a new process and token. */
  setupAgain(reason: string): void {
    this.api.clearSession();
    this.finish = null;
    this.backToToken(`The manager did not start: ${reason}`);
  }

  reload(): void {
    this.document.location.reload();
  }

  private started(health: SetupHealth): void {
    this.bootId = health.boot_id;
    if (health.mode === 'normal') {
      this.step = 'normal';
      return;
    }
    this.backToToken(health.last_failure ? `The last start failed: ${health.last_failure}` : '');
  }
}
