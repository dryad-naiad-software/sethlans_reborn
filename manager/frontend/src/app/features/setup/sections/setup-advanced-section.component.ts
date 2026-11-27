// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, Input } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { SetupFieldErrorsComponent } from '../setup-field-errors.component';
import { errorsFor } from '../setup-errors';
import { FieldErrors, SetupForm } from '../setup.types';
import { SetupDatabaseFieldsComponent } from './setup-database-fields.component';

const BIND_LABELS: Record<string, string> = {
  '0.0.0.0': '0.0.0.0 (every network)',
  '127.0.0.1': '127.0.0.1 (this machine only)',
};

/** Advanced (OQ-9): bind address, port and database, collapsed, defaults filled in. */
@Component({
  selector: 'app-setup-advanced-section',
  standalone: true,
  imports: [FormsModule, MatExpansionModule, MatFormFieldModule, MatInputModule,
    MatSelectModule, SetupFieldErrorsComponent, SetupDatabaseFieldsComponent],
  template: `
    <section>
      <mat-expansion-panel [expanded]="expanded">
        <mat-expansion-panel-header>
          <mat-panel-title>Advanced</mat-panel-title>
          <mat-panel-description>Address, port and database</mat-panel-description>
        </mat-expansion-panel-header>
        <app-setup-field-errors [messages]="err('server')" />
        <mat-form-field class="full-width">
          <mat-label>Bind address</mat-label>
          <mat-select [(ngModel)]="form.server.host">
            @for (address of form.bind_addresses; track address) {
              <mat-option [value]="address">{{ bindLabel(address) }}</mat-option>
            }
          </mat-select>
        </mat-form-field>
        <app-setup-field-errors [messages]="err('server.host')" />
        <mat-form-field class="full-width">
          <mat-label>Port</mat-label>
          <input matInput type="number" min="1024" max="65535" [(ngModel)]="form.server.port" />
        </mat-form-field>
        <app-setup-field-errors [messages]="err('server.port')" />
        <app-setup-database-fields [form]="form" [errors]="errors" />
      </mat-expansion-panel>
    </section>
  `,
})
export class SetupAdvancedSectionComponent {
  @Input({ required: true }) form!: SetupForm;
  @Input() errors: FieldErrors = {};
  /** Opened by the page when one of its fields has an error. */
  @Input() expanded = false;

  err(key: string): string[] {
    return errorsFor(this.errors, key);
  }

  bindLabel(address: string): string {
    return BIND_LABELS[address] ?? address;
  }
}
