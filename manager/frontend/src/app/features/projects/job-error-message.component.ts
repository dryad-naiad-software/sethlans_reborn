// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, Input } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';

/** Longest text put into the hover tooltip; the expanded view shows all of it. */
export const ERROR_TOOLTIP_MAX = 500;

let nextId = 0;

/**
 * Shows a job's failure reason as one truncated line of plain, selectable
 * text with a hover tooltip. A separate toggle button expands the text in
 * place to the full message in a scrollable block. Renders nothing at all
 * when the message is missing or blank. The text is interpolated, so it is
 * never parsed as HTML.
 *
 * The toggle is always shown: whether one line overflows depends on the
 * table's column width at render time, which changes with the viewport.
 */
@Component({
  selector: 'app-job-error-message',
  standalone: true,
  imports: [MatIconModule, MatTooltipModule],
  template: `
    @if (text) {
      <div class="job-error">
        <span class="job-error-text" [class.expanded]="expanded" [id]="textId"
              [attr.tabindex]="expanded ? 0 : null"
              [matTooltip]="tooltip" [matTooltipDisabled]="expanded"
              matTooltipTouchGestures="off">{{ text }}</span>
        <button type="button" class="job-error-toggle"
                [attr.aria-expanded]="expanded" [attr.aria-controls]="textId"
                [attr.aria-label]="expanded ? 'Hide full error' : 'Show full error'"
                (click)="expanded = !expanded">
          <mat-icon aria-hidden="true">{{ expanded ? 'expand_less' : 'expand_more' }}</mat-icon>
        </button>
      </div>
    }
  `,
  styles: [`
    :host { display: block; }
    .job-error {
      display: flex; align-items: flex-start; gap: 2px; max-width: 300px; margin-top: 2px;
      font-size: 12px; color: #c62828;
    }
    .job-error-text {
      flex: 1 1 auto; min-width: 0; line-height: 20px; user-select: text;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .job-error-text.expanded {
      white-space: pre-wrap; overflow-wrap: anywhere; overflow-y: auto; max-height: 160px;
      color: rgba(0,0,0,0.7);
    }
    .job-error-toggle {
      flex: none; display: inline-flex; align-items: center; justify-content: center;
      width: 20px; height: 20px; padding: 0; border: 0; border-radius: 50%;
      background: none; color: inherit; cursor: pointer;
    }
    .job-error-toggle:hover { background: rgba(0,0,0,0.06); }
    .job-error-toggle:focus-visible, .job-error-text:focus-visible {
      outline: 2px solid currentColor; outline-offset: 1px;
    }
    .job-error-toggle mat-icon { font-size: 18px; width: 18px; height: 18px; }
  `],
})
export class JobErrorMessageComponent {
  readonly textId = `job-error-text-${nextId++}`;
  text: string | null = null;
  tooltip = '';
  expanded = false;

  @Input() set message(value: string | null | undefined) {
    const trimmed = value?.trim() ?? '';
    this.text = trimmed || null;
    this.tooltip = trimmed.length > ERROR_TOOLTIP_MAX
      ? `${trimmed.slice(0, ERROR_TOOLTIP_MAX)}... (use the expand button)`
      : trimmed;
    if (!this.text) this.expanded = false;
  }
}
