// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { MatSnackBar } from '@angular/material/snack-bar';
import { Subject, of, throwError } from 'rxjs';
import { SequenceSubmission } from '../../core/services/sequence.types';
import { SequenceService } from '../../core/services/sequence.service';
import { SequenceVersionShotsComponent } from './sequence-version-shots.component';
import { HOSTILE, NO_DOWNLOADS, shot, text, version } from './sequence.fixtures';
import { machineRow } from '../../shared/machine-row.fixtures';

describe('SequenceVersionShotsComponent', () => {
  let fixture: ComponentFixture<SequenceVersionShotsComponent>;
  let sequences: jasmine.SpyObj<SequenceService>;
  let snackBar: jasmine.SpyObj<MatSnackBar>;

  async function setup(shots = [shot(), shot({ index: 2, name: 'sh020', status: 'ERROR', error_message: 'GPU lost' })]) {
    sequences = jasmine.createSpyObj<SequenceService>('SequenceService', ['pollDetail', 'get', 'rerenderShot', 'downloadUrl']);
    sequences.pollDetail.and.returnValue(of(version({ shots })));
    sequences.get.and.returnValue(of(version({ shots })));
    sequences.rerenderShot.and.returnValue(of({ status: 'QUEUED', requeued: 1 }));
    sequences.downloadUrl.and.callFake((id, kind, index) => `/dl/${id}/${index ?? 'all'}/${kind}`);
    snackBar = jasmine.createSpyObj<MatSnackBar>('MatSnackBar', ['open']);
    await TestBed.configureTestingModule({
      imports: [SequenceVersionShotsComponent, NoopAnimationsModule],
      providers: [
        { provide: SequenceService, useValue: sequences },
        { provide: MatSnackBar, useValue: snackBar },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(SequenceVersionShotsComponent);
    fixture.componentRef.setInput('submissionId', 7);
    fixture.detectChanges();
  }

  function rows(): HTMLElement[] {
    return Array.from(fixture.nativeElement.querySelectorAll('tr.shot'));
  }

  it('polls the version detail and lists each shot with its frames, status and error', async () => {
    await setup();
    expect(sequences.pollDetail).toHaveBeenCalledWith(7, jasmine.any(Function));
    expect(rows().length).toBe(2);
    expect(text(rows()[0])).toContain('sh010');
    expect(text(rows()[0])).toContain('0-47');
    expect(text(rows()[1].querySelector('.shot-status'))).toBe('ERROR');
    expect(text(rows()[1].querySelector('.shot-error'))).toBe('GPU lost');
  });

  it('links the shot downloads the API allows', async () => {
    await setup([shot({ downloads: { ...NO_DOWNLOADS, frames: true } })]);
    const links = Array.from(rows()[0].querySelectorAll('a.download'));
    expect(links.map(a => a.getAttribute('href'))).toEqual(['/dl/7/1/frames']);
  });

  it('shows shot names and errors as text only', async () => {
    await setup([shot({ name: HOSTILE, status: 'ERROR', error_message: HOSTILE })]);
    expect(text(rows()[0].querySelector('.shot-name'))).toBe(HOSTILE);
    expect(text(rows()[0].querySelector('.shot-error'))).toBe(HOSTILE);
    expect(rows()[0].querySelector('img, b')).toBeNull();
  });

  it('offers "Render again" only for failed or canceled shots and requeues the shot', async () => {
    await setup([shot(), shot({ index: 2, status: 'ERROR' }), shot({ index: 3, status: 'CANCELED' })]);
    expect(rows()[0].querySelector('button.rerender')).toBeNull();
    expect(rows()[2].querySelector('button.rerender')).not.toBeNull();
    let changed = 0;
    fixture.componentInstance.changed.subscribe(() => changed++);
    (rows()[1].querySelector('button.rerender') as HTMLButtonElement).click();
    expect(sequences.rerenderShot).toHaveBeenCalledWith(7, 2);
    expect(sequences.pollDetail).toHaveBeenCalledTimes(2);
    expect(changed).toBe(1);
  });

  it('shows the refusal of a re-render', async () => {
    await setup();
    sequences.rerenderShot.and.returnValue(throwError(() => ({ error: { error: 'The version was canceled.' } })));
    (rows()[1].querySelector('button.rerender') as HTMLButtonElement).click();
    expect(snackBar.open).toHaveBeenCalledWith('The version was canceled.', 'Dismiss', jasmine.any(Object));
    expect(fixture.componentInstance.busy).toBeFalse();
  });

  it('polls while the version is active and stops when it is final', async () => {
    await setup();
    const detail$ = new Subject<SequenceSubmission>();
    sequences.pollDetail.and.returnValue(detail$);
    fixture.componentInstance.load();
    detail$.next(version({ status: 'RENDERING', shots: [shot()] }));
    expect(detail$.observed).toBeTrue();
    detail$.next(version({ status: 'PARTLY_DONE', shots: [shot()] }));
    expect(detail$.observed).toBeFalse();
    expect(fixture.componentInstance.detail?.status).toBe('PARTLY_DONE');
  });

  it('shows the piece key\'s machine rows below the shots (FR-MT-U-4)', async () => {
    await setup();
    sequences.pollDetail.and.returnValue(of(version({
      shots: [shot()], machines: [machineRow({ state: 'does_not_fit', lower_bound_mb: 19012 }), machineRow({ covered: true })],
    })));
    fixture.componentInstance.load();
    fixture.detectChanges();
    const machines = Array.from(fixture.nativeElement.querySelectorAll('.machine-row'), (r: Element) => text(r));
    expect(machines).toEqual([
      'render-01, Radeon RX 7900 XT: does not fit: at least 19,012 MB on a 20,464 MB card',
      'render-01, Radeon RX 7900 XT: fits: 13,689 of 20,464 MB (covered)',
    ]);
  });

  it('shows no machine list before the version has pieces (machines null or absent)', async () => {
    await setup();
    expect(fixture.nativeElement.querySelector('.machines')).toBeNull();
    sequences.pollDetail.and.returnValue(of(version({ shots: [shot()], machines: null })));
    fixture.componentInstance.load();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.machines')).toBeNull();
    expect(rows().length).toBe(1);
  });

  it('asks for a new version of a shot', async () => {
    await setup();
    const asked: string[] = [];
    fixture.componentInstance.newVersion.subscribe(name => asked.push(name));
    (rows()[1].querySelector('button.new-version') as HTMLButtonElement).click();
    expect(asked).toEqual(['sh020']);
  });
});
