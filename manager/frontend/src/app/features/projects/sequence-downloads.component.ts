// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, Input, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { SequenceDownload, SequenceService } from '../../core/services/sequence.service';
import { Downloads } from '../../core/services/sequence.types';

const LABELS: { kind: SequenceDownload; label: string }[] = [
  { kind: 'video', label: 'Video' },
  { kind: 'video_sound', label: 'Video with sound' },
  { kind: 'sound', label: 'Sound' },
  { kind: 'frames', label: 'Frames (zip)' },
];

/**
 * The downloads of a version or of one shot (FR-SL-1, FR-SL-4): a plain
 * link per available item (FR-SU-4), none for an item the API says is not
 * available.
 */
@Component({
  selector: 'app-sequence-downloads',
  standalone: true,
  imports: [MatButtonModule],
  template: `
    @for (item of available(); track item.kind) {
      <a mat-button class="download" [attr.data-kind]="item.kind" [href]="url(item.kind)" download>{{ item.label }}</a>
    }
  `,
})
export class SequenceDownloadsComponent {
  private readonly sequences = inject(SequenceService);

  @Input({ required: true }) submissionId!: number;
  @Input() shotIndex?: number;
  @Input() downloads: Downloads | null = null;

  available(): { kind: SequenceDownload; label: string }[] {
    const flags = this.downloads;
    return flags ? LABELS.filter(item => flags[item.kind]) : [];
  }

  url(kind: SequenceDownload): string {
    return this.sequences.downloadUrl(this.submissionId, kind, this.shotIndex);
  }
}
