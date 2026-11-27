// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import {
  Component, ElementRef, EventEmitter, Injector, Input, Output, ViewChild, afterNextRender, inject,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatRadioModule } from '@angular/material/radio';
import { SetupApiService } from './setup-api.service';
import {
  errorsFor, formNotices, otherErrors, sectionHasErrors, sectionsFor,
} from './setup-errors';
import { SetupFieldErrorsComponent } from './setup-field-errors.component';
import {
  FieldCodes, FieldErrors, FinishInfo, PasswordConfirm, SetupFailure, SetupForm, SetupMode,
} from './setup.types';
import { SetupAdminSectionComponent } from './sections/setup-admin-section.component';
import { SetupAdvancedSectionComponent } from './sections/setup-advanced-section.component';
import { SetupDataFolderSectionComponent } from './sections/setup-data-folder-section.component';
import { SetupManagerSectionComponent } from './sections/setup-manager-section.component';
import { SetupWorkerSectionComponent } from './sections/setup-worker-section.component';

const MODE_LABELS: Record<SetupMode, string> = {
  manager: 'Manager only',
  manager_worker: 'Manager and worker',
  worker: 'Worker only',
};

/** The one setup form, sections by mode (FR-WZ-3, FR-WZ-10), sent once (FR-WZ-2). */
@Component({
  selector: 'app-setup-form',
  standalone: true,
  imports: [FormsModule, MatButtonModule, MatProgressSpinnerModule, MatRadioModule,
    SetupFieldErrorsComponent, SetupAdminSectionComponent, SetupAdvancedSectionComponent,
    SetupDataFolderSectionComponent, SetupManagerSectionComponent, SetupWorkerSectionComponent],
  template: `
    @if (form.last_failure) {
      <p class="setup-error" role="alert">The last start failed: {{ form.last_failure }}</p>
    }
    @for (line of notices(); track $index) {
      <p class="setup-warning">{{ line }}</p>
    }
    @if (form.leftovers.length) {
      <p class="setup-note">Files of an earlier setup are still here; they are not used:</p>
      <ul class="setup-note">
        @for (path of form.leftovers; track path) {
          <li class="setup-mono">{{ path }}</li>
        }
      </ul>
    }

    <section>
      <h2>Mode</h2>
      <mat-radio-group class="modes" [(ngModel)]="form.mode" aria-label="Mode">
        @for (mode of form.modes; track mode) {
          <mat-radio-button [value]="mode">{{ modeLabel(mode) }}</mat-radio-button>
        }
      </mat-radio-group>
      <app-setup-field-errors [messages]="err('mode')" />
      <p class="setup-note">This machine: {{ form.hostname }}
        {{ form.addresses.length ? '(' + form.addresses.join(', ') + ')' : '' }}</p>
    </section>

    @if (shows('admin')) {
      <app-setup-admin-section [form]="form" [errors]="errors" [codes]="codes"
                               [confirm]="confirm" />
    }
    @if (shows('advanced')) {
      <app-setup-advanced-section [form]="form" [errors]="errors"
                                  [expanded]="hasErrors('advanced')" />
    }
    @if (shows('worker')) {
      <app-setup-worker-section [form]="form" [errors]="errors" [confirm]="confirm" />
    }
    @if (shows('manager')) {
      <app-setup-manager-section [form]="form" [errors]="errors" />
    }
    <app-setup-data-folder-section [form]="form" [errors]="errors" />

    @if (message) {
      <p #summary class="setup-error" role="alert" tabindex="-1">{{ message }}</p>
    }
    @for (line of others(); track $index) {
      <p class="setup-error">{{ line }}</p>
    }
    @if (!message && (hasAnyErrors() || others().length)) {
      <p #summary class="setup-error" role="alert" tabindex="-1">
        Some fields need a change; see the messages above.
      </p>
    }
    <button mat-raised-button color="primary" type="button" (click)="submit()"
            [disabled]="submitting">
      Save and start
    </button>
    @if (submitting) {
      <p class="setup-note saving">
        <mat-spinner diameter="20" />
        Checking and saving; a database test or enrolling the worker can take a while.
      </p>
    }
  `,
  styles: [`
    .modes { display: flex; flex-direction: column; gap: 4px; margin: 0 0 8px; }
    .saving { display: flex; align-items: center; gap: 8px; margin-top: 12px; }
  `],
})
export class SetupFormComponent {
  private readonly api = inject(SetupApiService);
  private readonly injector = inject(Injector);

  @Input({ required: true }) form!: SetupForm;
  @Output() finished = new EventEmitter<FinishInfo>();
  /** The session expired or was replaced: the page asks for the token again. */
  @Output() sessionLost = new EventEmitter<string>();

  errors: FieldErrors = {};
  codes: FieldCodes = {};
  message = '';
  submitting = false;
  confirm: PasswordConfirm = { admin: '', worker: '' };

  modeLabel(mode: SetupMode): string {
    return MODE_LABELS[mode] ?? mode;
  }

  shows(section: string): boolean {
    return sectionsFor(this.form.mode).includes(section);
  }

  err(key: string): string[] {
    return errorsFor(this.errors, key);
  }

  hasErrors(section: string): boolean {
    return sectionHasErrors(this.errors, section);
  }

  /** Any message on a section this mode shows. */
  hasAnyErrors(): boolean {
    return sectionsFor(this.form.mode).some(section => this.hasErrors(section));
  }

  notices(): string[] {
    return formNotices(this.form.errors, this.form.mode);
  }

  others(): string[] {
    return otherErrors(this.errors);
  }

  /** The one alert line after a failed submit: the message, or the "need a change" line. */
  @ViewChild('summary') private summary?: ElementRef<HTMLElement>;

  submit(): void {
    if (this.submitting) {
      return;
    }
    this.message = this.mismatch();
    if (this.message) {
      this.focusSummary();
      return;
    }
    this.submitting = true;
    this.errors = {};
    this.codes = {};
    this.api.submit(this.form).subscribe({
      next: info => {
        this.submitting = false;
        this.finished.emit(info);
      },
      error: (failure: SetupFailure) => this.failed(failure),
    });
  }

  private failed(failure: SetupFailure): void {
    this.submitting = false;
    if (failure.status === 403 && failure.code === 'not_authenticated') {
      this.api.clearSession();
      this.sessionLost.emit('The setup session ended; enter the setup token again.');
      return;
    }
    this.errors = failure.fields;
    this.codes = failure.codes;
    // Field messages are shown on their fields (a worker refusal repeats its
    // message on one); 409, 500 and a field-less 400 show the message.
    const hasFields = Object.keys(failure.fields).length > 0;
    this.message = hasFields ? '' : failure.message;
    this.focusSummary();
  }

  /** Moves the focus to the summary line once it is drawn. */
  private focusSummary(): void {
    afterNextRender(() => this.summary?.nativeElement.focus(), { injector: this.injector });
  }

  /** The "again" fields of the passwords shown in this mode. */
  private mismatch(): string {
    const adminShown = this.shows('admin') && !this.form.admin.keep_existing;
    if (adminShown && this.confirm.admin !== this.form.admin.password) {
      return 'The admin passwords are not the same.';
    }
    if (this.shows('worker') && this.confirm.worker !== this.form.worker.ui_password) {
      return 'The worker page passwords are not the same.';
    }
    return '';
  }
}
