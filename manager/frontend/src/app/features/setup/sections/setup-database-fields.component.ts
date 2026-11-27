// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, Input, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { SetupFieldErrorsComponent } from '../setup-field-errors.component';
import { errorsFor } from '../setup-errors';
import { FieldErrors, SetupForm } from '../setup.types';

const DEFAULT_PORTS: Record<string, number> = { postgresql: 5432, mysql: 3306 };

/** As the server's `net.is_loopback_host`: `localhost`, 127.0.0.0/8 or ::1. */
export function isLoopbackHost(host: string): boolean {
  const text = host.trim().toLowerCase().replace(/^\[|\]$/g, '');
  return text === 'localhost' || text === '::1' || /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(text);
}

/** The database fields of Advanced (FR-WZ-5, FR-WZ-7b). */
@Component({
  selector: 'app-setup-database-fields',
  standalone: true,
  imports: [FormsModule, MatCheckboxModule, MatFormFieldModule, MatInputModule,
    MatSelectModule, SetupFieldErrorsComponent],
  template: `
    @let db = form.database;
    <h2>Database</h2>
    <app-setup-field-errors [messages]="err('database')" />
    <mat-form-field class="full-width">
      <mat-label>Database</mat-label>
      <mat-select [(ngModel)]="db.engine">
        <mat-option value="sqlite">SQLite (a file on this machine)</mat-option>
        <mat-option value="postgresql">PostgreSQL</mat-option>
        <mat-option value="mysql">MySQL or MariaDB</mat-option>
      </mat-select>
    </mat-form-field>
    <app-setup-field-errors [messages]="err('database.engine')" />
    @if (db.engine === 'sqlite') {
      <mat-form-field class="full-width">
        <mat-label>Database file (optional)</mat-label>
        <input matInput [(ngModel)]="db.name" placeholder="Empty: in the data folder" />
        <mat-hint>An absolute path, or empty for the default file.</mat-hint>
      </mat-form-field>
      <app-setup-field-errors [messages]="err('database.name')" />
    } @else {
      <mat-form-field class="full-width">
        <mat-label>Host</mat-label>
        <input matInput [(ngModel)]="db.host" (ngModelChange)="hostChanged($event)" />
      </mat-form-field>
      <app-setup-field-errors [messages]="err('database.host')" />
      <mat-form-field class="full-width">
        <mat-label>Port</mat-label>
        <input matInput type="number" min="1" max="65535" [(ngModel)]="db.port"
               [placeholder]="defaultPort()" />
      </mat-form-field>
      <app-setup-field-errors [messages]="err('database.port')" />
      <mat-form-field class="full-width">
        <mat-label>Database name</mat-label>
        <input matInput [(ngModel)]="db.name" />
      </mat-form-field>
      <app-setup-field-errors [messages]="err('database.name')" />
      <mat-form-field class="full-width">
        <mat-label>User</mat-label>
        <input matInput [(ngModel)]="db.user" autocomplete="off" />
      </mat-form-field>
      <app-setup-field-errors [messages]="err('database.user')" />
      <mat-form-field class="full-width">
        <mat-label>Password</mat-label>
        <input matInput type="password" [(ngModel)]="db.password" autocomplete="new-password"
               [placeholder]="db.password_set ? 'Empty: keep the stored password' : ''" />
      </mat-form-field>
      <app-setup-field-errors [messages]="err('database.password')" />
      <mat-checkbox class="setup-block" [(ngModel)]="db.tls" (ngModelChange)="tlsTouched = true">
        Use TLS
      </mat-checkbox>
      <app-setup-field-errors [messages]="err('database.tls')" />
      @if (db.tls) {
        @if (db.engine === 'postgresql') {
          <mat-form-field class="full-width">
            <mat-label>TLS mode</mat-label>
            <mat-select [(ngModel)]="db.tls_mode">
              <mat-option value="verify-full">verify-full (certificate and host name)</mat-option>
              <mat-option value="verify-ca">verify-ca (certificate only)</mat-option>
              <mat-option value="require">require (no certificate check)</mat-option>
            </mat-select>
          </mat-form-field>
          <app-setup-field-errors [messages]="err('database.tls_mode')" />
        }
        <mat-form-field class="full-width">
          <mat-label>CA file</mat-label>
          <input matInput [(ngModel)]="db.tls_ca_file" />
          <mat-hint>Absolute path of the CA certificate that signed the server's certificate.</mat-hint>
        </mat-form-field>
        <app-setup-field-errors [messages]="err('database.tls_ca_file')" />
      } @else if (db.host.trim() && !isLoopback(db.host)) {
        <p class="setup-warning" role="alert">
          The connection to {{ db.host }} will not be encrypted (TLS is off).
        </p>
      }
    }
  `,
})
export class SetupDatabaseFieldsComponent implements OnInit {
  @Input({ required: true }) form!: SetupForm;
  @Input() errors: FieldErrors = {};

  /**
   * Until the person sets "Use TLS", it follows the host: on unless
   * loopback (FR-WZ-7b). A stored PostgreSQL or MySQL setting keeps its
   * stored value.
   */
  tlsTouched = false;

  ngOnInit(): void {
    this.tlsTouched = this.form.database.engine !== 'sqlite';
  }

  err(key: string): string[] {
    return errorsFor(this.errors, key);
  }

  isLoopback(host: string): boolean {
    return isLoopbackHost(host);
  }

  defaultPort(): string {
    return String(DEFAULT_PORTS[this.form.database.engine] ?? '');
  }

  hostChanged(host: string): void {
    if (!this.tlsTouched) {
      this.form.database.tls = host.trim() !== '' && !isLoopbackHost(host);
    }
  }
}
