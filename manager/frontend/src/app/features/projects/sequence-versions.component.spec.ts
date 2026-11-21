// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatDialog } from '@angular/material/dialog';
import { MatSnackBar } from '@angular/material/snack-bar';
import { Subject, of, throwError } from 'rxjs';
import { SequenceService } from '../../core/services/sequence.service';
import { SequenceShot, SequenceSubmission } from '../../core/services/sequence.types';
import { SequenceVersionsComponent } from './sequence-versions.component';
import { ALL_DOWNLOADS, HOSTILE, NO_DOWNLOADS, shot, text, version } from './sequence.fixtures';

describe('SequenceVersionsComponent', () => {
  let fixture: ComponentFixture<SequenceVersionsComponent>;
  let sequences: jasmine.SpyObj<SequenceService>;
  let snackBar: jasmine.SpyObj<MatSnackBar>;
  let dialog: jasmine.SpyObj<MatDialog>;
  let rows$: Subject<SequenceSubmission[]>;
  let detailShots: SequenceShot[];

  beforeEach(async () => {
    rows$ = new Subject();
    detailShots = [shot()];
    sequences = jasmine.createSpyObj<SequenceService>('SequenceService', [
      'pollList', 'list', 'pollDetail', 'get', 'cancel', 'delete', 'retrySound', 'retryVideo', 'mergeSound',
      'rerenderShot', 'downloadUrl',
    ]);
    sequences.pollList.and.returnValue(rows$);
    sequences.list.and.returnValue(of([]));
    sequences.get.and.callFake(id => of(version({ id, shots: detailShots })));
    sequences.pollDetail.and.returnValue(of(version({ shots: [shot()] })));
    sequences.downloadUrl.and.callFake((id, kind, index) => `/dl/${id}/${index ?? 'all'}/${kind}`);
    for (const name of ['cancel', 'delete', 'retrySound', 'retryVideo', 'mergeSound'] as const) {
      (sequences[name] as jasmine.Spy).and.returnValue(of({}));
    }
    snackBar = jasmine.createSpyObj<MatSnackBar>('MatSnackBar', ['open']);
    dialog = jasmine.createSpyObj<MatDialog>('MatDialog', ['open']);
    dialog.open.and.returnValue({ afterClosed: () => of(true) } as never);
    await TestBed.configureTestingModule({
      imports: [SequenceVersionsComponent, NoopAnimationsModule],
      providers: [
        { provide: SequenceService, useValue: sequences },
        { provide: MatSnackBar, useValue: snackBar },
        { provide: MatDialog, useValue: dialog },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(SequenceVersionsComponent);
    fixture.componentRef.setInput('projectId', 'unreal-uuid');
    fixture.detectChanges();
  });

  /** A fresh poll answering `rows` (each call restarts polling, as an action does). */
  function show(rows: SequenceSubmission[]): HTMLElement {
    rows$ = new Subject();
    sequences.pollList.and.returnValue(rows$);
    fixture.componentInstance.refresh();
    rows$.next(rows);
    fixture.detectChanges();
    return fixture.nativeElement;
  }

  function button(el: Element, cls: string): HTMLButtonElement | null {
    return el.querySelector(`button.${cls}`);
  }

  it('polls the project\'s versions', () => {
    expect(sequences.pollList).toHaveBeenCalledWith('unreal-uuid', jasmine.any(Function));
    expect(text(show([]).querySelector('.empty'))).toBe('No sequence versions yet.');
  });

  it('shows status, partly done, shot counts and the routing note', () => {
    const el = show([version({ status: 'PARTLY_DONE', shots_done: 1, shots_error: 1, routing_note: 'No card fits.' })]);
    expect(text(el.querySelector('.version-status'))).toBe('Partly done');
    expect(text(el.querySelector('.version-shots'))).toBe('Shots 1/2 (1 failed)');
    expect(text(el.querySelector('.routing-note'))).toBe('No card fits.');
  });

  it('shows project text only as text', () => {
    const el = show([version({ name: HOSTILE, warnings: [HOSTILE], error_message: HOSTILE, routing_note: HOSTILE })]);
    expect(text(el.querySelector('.version-name'))).toBe(HOSTILE);
    expect(text(el.querySelector('.version-warning'))).toBe(HOSTILE);
    expect(text(el.querySelector('.version-error'))).toBe(HOSTILE);
    expect(el.querySelector('.version img, .version b')).toBeNull();
  });

  it('links only the downloads the API says are available', () => {
    let el = show([version({ downloads: NO_DOWNLOADS })]);
    expect(el.querySelectorAll('a.download').length).toBe(0);
    el = show([version({ downloads: { ...NO_DOWNLOADS, sound: true, frames: true } })]);
    const links = Array.from(el.querySelectorAll('a.download')) as HTMLAnchorElement[];
    expect(links.map(a => a.getAttribute('data-kind'))).toEqual(['sound', 'frames']);
    expect(links[0].getAttribute('href')).toBe('/dl/7/all/sound');
    el = show([version({ downloads: ALL_DOWNLOADS })]);
    expect(el.querySelectorAll('a.download').length).toBe(4);
  });

  it('offers cancel only while active', () => {
    expect(button(show([version({ status: 'RENDERING' })]), 'cancel')).not.toBeNull();
    expect(button(show([version({ status: 'DONE' })]), 'cancel')).toBeNull();
  });

  it('offers no retry on a finished version without a failed item', () => {
    const el = show([version({ status: 'PARTLY_DONE' })]);
    expect(button(el, 'retry-sound')).toBeNull();
    expect(button(el, 'retry-video')).toBeNull();
  });

  it('offers Retry sound for a failed sound render, of the version or of a shot', () => {
    const failed = { sound_status: 'ERROR' as const, sound_error: 'The sound render failed: GPU lost' };
    expect(button(show([version(failed)]), 'retry-sound')).not.toBeNull();
    detailShots = [shot(failed)];
    const el = show([version({ status: 'PARTLY_DONE' })]);
    expect(sequences.get).toHaveBeenCalledWith(7);
    expect(button(el, 'retry-sound')).not.toBeNull();
    expect(button(el, 'retry-video')).toBeNull();
  });

  it('offers Retry video for a failed video, merged copy or sound cut, of the version or of a shot', () => {
    expect(button(show([version({ video_status: 'ERROR', video_error: 'x' })]), 'retry-video')).not.toBeNull();
    detailShots = [shot({ sound_status: 'ERROR', sound_error: 'Cutting failed.' })];
    let el = show([version({ status: 'PARTLY_DONE' })]);
    expect(button(el, 'retry-video')).not.toBeNull();
    expect(button(el, 'retry-sound')).toBeNull();
    detailShots = [shot({ sound_video_status: 'ERROR' })];
    el = show([version({ status: 'ERROR' })]);
    expect(button(el, 'retry-video')).not.toBeNull();
  });

  it('offers no retry or merge on a canceled version', () => {
    detailShots = [shot({ video_status: 'ERROR' })];
    const el = show([version({ status: 'CANCELED', sound_status: 'ERROR', sound_error: 'The sound render failed' })]);
    expect(button(el, 'retry-sound')).toBeNull();
    expect(button(el, 'retry-video')).toBeNull();
    expect(button(el, 'merge-sound')).toBeNull();
  });

  it('offers Merge sound only for a version with video and sound that does not merge yet', () => {
    expect(button(show([version()]), 'merge-sound')).not.toBeNull();
    expect(button(show([version({ merge_sound: true })]), 'merge-sound')).toBeNull();
    expect(button(show([version({ sound: false })]), 'merge-sound')).toBeNull();
    expect(button(show([version({ video_settings: null })]), 'merge-sound')).toBeNull();
    expect(button(show([version({ status: 'DISCOVERING' })]), 'merge-sound')).toBeNull();
  });

  it('runs the actions and restarts polling', () => {
    const el = show([version({ video_status: 'ERROR', sound_status: 'ERROR', sound_error: 'The sound render x' })]);
    const polls = sequences.pollList.calls.count();
    button(el, 'retry-sound')!.click();
    button(el, 'retry-video')!.click();
    button(el, 'merge-sound')!.click();
    expect(sequences.retrySound).toHaveBeenCalledWith(7);
    expect(sequences.retryVideo).toHaveBeenCalledWith(7);
    expect(sequences.mergeSound).toHaveBeenCalledWith(7);
    expect(sequences.pollList.calls.count()).toBe(polls + 3);
  });

  it('keeps polling while a version is active and stops when all are final', () => {
    show([version({ status: 'RENDERING' }), version({ id: 8 })]);
    expect(rows$.observed).toBeTrue();
    rows$.next([version(), version({ id: 8, status: 'CANCELED' })]);
    expect(rows$.observed).toBeFalse();
    show([version()]);
    expect(rows$.observed).toBeFalse();
  });

  it('cancels and deletes after confirmation', () => {
    let el = show([version({ status: 'QUEUED' })]);
    button(el, 'cancel')!.click();
    expect(dialog.open).toHaveBeenCalled();
    expect(sequences.cancel).toHaveBeenCalledWith(7);
    el = show([version()]);
    button(el, 'delete')!.click();
    expect(sequences.delete).toHaveBeenCalledWith(7);
  });

  it('does nothing when the confirmation is declined', () => {
    dialog.open.and.returnValue({ afterClosed: () => of(false) } as never);
    button(show([version()]), 'delete')!.click();
    expect(sequences.delete).not.toHaveBeenCalled();
  });

  it('shows the server\'s refusal', () => {
    sequences.delete.and.returnValue(throwError(() => ({ error: { error: 'A render of this version is still running.' } })));
    button(show([version()]), 'delete')!.click();
    expect(snackBar.open).toHaveBeenCalledWith('A render of this version is still running.', 'Dismiss', jasmine.any(Object));
  });

  it('opens a version\'s shots and passes "new version of this shot" up', () => {
    const asked: string[] = [];
    fixture.componentInstance.newShotVersion.subscribe(r => asked.push(`${r.version.id}:${r.shot}`));
    const el = show([version()]);
    button(el, 'toggle-shots')!.click();
    fixture.detectChanges();
    expect(sequences.pollDetail).toHaveBeenCalledWith(7, jasmine.any(Function));
    button(el, 'new-version')!.click();
    expect(asked).toEqual(['7:sh010']);
  });
});
