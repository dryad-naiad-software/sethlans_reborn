// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, Input } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatRadioModule } from '@angular/material/radio';
import { SetupFieldErrorsComponent } from '../setup-field-errors.component';
import { errorsFor } from '../setup-errors';
import { DiscoveredManager, FieldErrors, SetupForm } from '../setup.types';

/**
 * Manager and Enrollment key, worker only (FR-WZ-3, FR-RS-3): a manager
 * found on the network (name, host, the URL contacted, manager id, port)
 * or a typed URL (trimmed on submit), and
 * its enrollment key; or "Keep enrollment with <manager>".
 */
@Component({
  selector: 'app-setup-manager-section',
  standalone: true,
  imports: [FormsModule, MatCheckboxModule, MatFormFieldModule, MatInputModule,
    MatRadioModule, SetupFieldErrorsComponent],
  template: `
    @let manager = form.manager;
    <section>
      <h2>Manager</h2>
      <app-setup-field-errors [messages]="err('manager')" />
      @if (form.enrollment) {
        <mat-checkbox class="setup-block" [(ngModel)]="manager.keep_enrollment">
          Keep enrollment with {{ enrolledName() }}
        </mat-checkbox>
      }
      <app-setup-field-errors [messages]="err('manager.keep_enrollment')" />
      @if (!manager.keep_enrollment) {
        @if (form.managers.length) {
          <p class="setup-note">Managers found on the network:</p>
          <mat-radio-group class="managers" [ngModel]="manager.url"
                           (ngModelChange)="choose($event)" aria-label="Managers found">
            @for (found of form.managers; track $index) {
              <mat-radio-button [value]="found.url" [disabled]="!found.url">
                <strong>{{ found.name || 'Unnamed manager' }}</strong>
                <span class="setup-note setup-block">
                  {{ found.host || found.ip }}, port {{ found.port }}<br />
                  <span class="setup-mono">{{ found.url || 'no address to contact' }}</span><br />
                  id <span class="setup-mono">{{ found.manager_id }}</span>
                  {{ found.version ? ', version ' + found.version : '' }}
                </span>
              </mat-radio-button>
            }
          </mat-radio-group>
        } @else {
          <p class="setup-note">No manager was found on the network; type its address.</p>
        }
        <mat-form-field class="full-width">
          <mat-label>Manager address</mat-label>
          <input matInput [ngModel]="manager.url" (ngModelChange)="choose($event)"
                 placeholder="https://host:8080" autocomplete="off" spellcheck="false" />
        </mat-form-field>
        <app-setup-field-errors [messages]="err('manager.url')" />
        <app-setup-field-errors [messages]="err('manager.name')" />

        <h2>Enrollment key</h2>
        <mat-form-field class="full-width">
          <mat-label>Enrollment key</mat-label>
          <input matInput [(ngModel)]="manager.enrollment_key" autocomplete="off"
                 spellcheck="false" />
          <mat-hint>Shown on the manager; 16 letters and digits.</mat-hint>
        </mat-form-field>
        <app-setup-field-errors [messages]="err('manager.enrollment_key')" />
      }
    </section>
  `,
  styles: [`
    .managers { display: flex; flex-direction: column; gap: 8px; margin: 0 0 16px; }
  `],
})
export class SetupManagerSectionComponent {
  @Input({ required: true }) form!: SetupForm;
  @Input() errors: FieldErrors = {};

  err(key: string): string[] {
    return errorsFor(this.errors, key);
  }

  /** A picked or typed URL; the name goes with a URL of the list only. */
  choose(url: string | null): void {
    const value = url ?? '';
    const found: DiscoveredManager | undefined =
      this.form.managers.find(entry => entry.url !== null && entry.url === value.trim());
    this.form.manager.url = value;
    this.form.manager.name = found?.name ?? '';
  }

  enrolledName(): string {
    const enrollment = this.form.enrollment;
    if (!enrollment) {
      return '';
    }
    // The worker's saved name first; a manager found now with the same id next.
    const found = enrollment.manager_id
      ? this.form.managers.find(entry => entry.manager_id === enrollment.manager_id)
      : undefined;
    const name = enrollment.manager_name || found?.name || '';
    const host = enrollment.host ?? '';
    const where = host && enrollment.port ? `${host}:${enrollment.port}` : host;
    if (name) {
      return where ? `${name} (${where})` : name;
    }
    return where || enrollment.manager_id || 'the current manager';
  }
}
