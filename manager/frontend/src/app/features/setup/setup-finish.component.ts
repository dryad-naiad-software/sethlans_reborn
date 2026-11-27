// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import {
  Component, DOCUMENT, EventEmitter, Input, OnDestroy, OnInit, Output, inject,
} from '@angular/core';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { Subscription, interval, take } from 'rxjs';
import { SetupApiService } from './setup-api.service';
import { RestartState, watchRestart } from './setup-restart-watch';
import { FinishInfo } from './setup.types';

export const REDIRECT_SECONDS = 30;

/**
 * The finish page (FR-WZ-8). Manager modes: wait for the manager on this
 * address (health every second, up to 180 s), then go to /login/; when
 * setup mode is back instead, the page shows its reason and the form. A
 * new address (port or bind address) cannot be polled from this page, so
 * it is shown and opened after 30 s. Worker only: the result, and the page
 * is done.
 */
@Component({
  selector: 'app-setup-finish',
  standalone: true,
  imports: [MatProgressSpinnerModule],
  template: `
    <section>
      <h2>Setup is saved</h2>
      @if (info.first_admin) {
        <p>Admin account: {{ info.first_admin }}</p>
      }
      @if (info.worker; as worker) {
        @if (worker.enrolled || worker.kept_enrollment) {
          <p>
            {{ worker.enrolled ? 'This worker is enrolled with' : 'This worker stays enrolled with' }}
            {{ worker.manager_name || worker.manager_url || 'the manager' }}.
          </p>
          <dl>
            @if (worker.manager_url) { <dt>Manager</dt><dd class="setup-mono">{{ worker.manager_url }}</dd> }
            @if (worker.manager_id) { <dt>Manager id</dt><dd class="setup-mono">{{ worker.manager_id }}</dd> }
            @if (worker.cert_fingerprint) {
              <dt>Certificate fingerprint</dt><dd class="setup-mono">{{ worker.cert_fingerprint }}</dd>
            }
            @if (worker.status) { <dt>Status</dt><dd>{{ worker.status }}</dd> }
          </dl>
        }
      }
      @for (line of info.warnings; track $index) {
        <p class="setup-warning">{{ line }}</p>
      }

      @if (!info.restarting) {
        <p>The worker starts now. This page will now close; you can close this tab.</p>
      } @else if (newAddress) {
        <p>Sethlans is restarting. The manager will answer at
          <a [href]="info.url" class="setup-mono">{{ info.url }}</a>.</p>
        <p class="setup-note">This page goes there in {{ countdown }} s.</p>
      } @else {
        @switch (state.kind) {
          @case ('waiting') {
            <p class="restarting"><mat-spinner diameter="20" /> Sethlans is restarting…</p>
          }
          @case ('done') {
            <p>The manager is running; opening the login page.</p>
          }
          @case ('timeout') {
            <p class="setup-error" role="alert">
              The manager has not answered for 3 minutes. Check the launcher's window or log, then
              open <a [href]="info.url">{{ info.url }}</a>.
            </p>
          }
        }
      }
    </section>
  `,
  styles: [`
    .restarting { display: flex; align-items: center; gap: 8px; }
    dt { font-weight: 500; }
    dd { margin: 0 0 8px; }
  `],
})
export class SetupFinishComponent implements OnInit, OnDestroy {
  private readonly api = inject(SetupApiService);
  private readonly document = inject(DOCUMENT);

  @Input({ required: true }) info!: FinishInfo;
  /** The setup process's `boot_id`, read before the submit. */
  @Input() bootId = '';
  /** Setup mode is back (the first start failed): the reason. */
  @Output() setupAgain = new EventEmitter<string>();

  state: RestartState = { kind: 'waiting' };
  newAddress = false;
  countdown = REDIRECT_SECONDS;
  private subscription?: Subscription;

  ngOnInit(): void {
    if (!this.info.restarting) {
      return;
    }
    this.newAddress = this.info.port_changed || !this.sameOrigin(this.info.url);
    if (this.newAddress) {
      this.subscription = interval(1000).pipe(take(REDIRECT_SECONDS)).subscribe(() => {
        this.countdown -= 1;
        if (this.countdown === 0 && this.info.url) {
          this.go(this.info.url);
        }
      });
      return;
    }
    this.subscription = watchRestart(() => this.api.health(), this.bootId).subscribe(state => {
      this.state = state;
      if (state.kind === 'done') {
        this.go('/login/');
      } else if (state.kind === 'setup_again') {
        this.setupAgain.emit(state.reason);
      }
    });
  }

  ngOnDestroy(): void {
    this.subscription?.unsubscribe();
  }

  private sameOrigin(url: string | null): boolean {
    if (!url) {
      return true;
    }
    try {
      return new URL(url).origin === this.document.location.origin;
    } catch {
      return false;
    }
  }

  private go(url: string): void {
    this.document.location.assign(url);
  }
}
