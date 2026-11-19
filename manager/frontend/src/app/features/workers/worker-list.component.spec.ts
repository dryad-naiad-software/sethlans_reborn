// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { Subject } from 'rxjs';
import { WorkerListComponent } from './worker-list.component';
import { WorkerService, Worker } from '../../core/services/worker.service';
import {
  HOSTILE_URL, makeAlert, makeCard, makeRequest, makeStatus, makeWorker,
} from './worker-cards.fixtures';

describe('WorkerListComponent', () => {
  let fixture: ComponentFixture<WorkerListComponent>;
  let list$: Subject<Worker[]>;

  beforeEach(async () => {
    list$ = new Subject<Worker[]>();
    const service = jasmine.createSpyObj('WorkerService', ['pollList', 'dismissCardAlert']);
    service.pollList.and.returnValue(list$.asObservable());
    await TestBed.configureTestingModule({
      imports: [WorkerListComponent, NoopAnimationsModule],
      providers: [{ provide: WorkerService, useValue: service }],
    }).compileComponents();
    fixture = TestBed.createComponent(WorkerListComponent);
    fixture.detectChanges();
  });

  function emit(workers: Worker[]): HTMLElement {
    list$.next(workers);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  function workerRows(el: HTMLElement): HTMLElement[] {
    return Array.from(el.querySelectorAll<HTMLElement>('tr.worker-row'));
  }

  it('marks a worker with an alert and not one without (AC-18)', () => {
    const el = emit([
      makeWorker({ id: 1, card_status: makeStatus({ alerts: [makeAlert(), makeAlert({ id: 'c'.repeat(32) })] }) }),
      makeWorker({ id: 2, hostname: 'render-02' }),
    ]);
    const [alerted, clean] = workerRows(el);
    expect(alerted.classList).toContain('card-alert-row');
    expect(alerted.querySelector('.card-alert-chip')!.textContent).toContain('Card alert (2)');
    expect(clean.classList).not.toContain('card-alert-row');
    expect(clean.querySelector('.card-alert-chip')).toBeNull();
    expect(alerted.querySelector('mat-chip-set')!.hasAttribute('aria-label')).toBeFalse();
  });

  it('shows "Change pending" only for a worker with a pending request (FR-CF-2)', () => {
    const el = emit([
      makeWorker({ id: 1, card_choice_request: makeRequest() }),
      makeWorker({ id: 2, card_choice_request: null }),
      makeWorker({ id: 3, card_choice_request: {} as Worker['card_choice_request'] }),
      makeWorker({ id: 4, card_choice_request: makeRequest(), card_status: makeStatus({ alerts: [makeAlert()] }) }),
    ]);
    const chips = workerRows(el).map(r =>
      Array.from(r.querySelectorAll('mat-chip')).map(c => c.textContent!.trim()));
    expect(chips).toEqual([['Change pending'], [], [], ['Card alert (1)', 'Change pending']]);
  });

  it('shows the ticked card names, else gpu_name', () => {
    const el = emit([
      makeWorker({ id: 1, card_status: makeStatus({ cards: [makeCard({ name: 'RTX 5090' })] }) }),
      makeWorker({ id: 2, card_status: null, gpu_name: 'GTX 1080' }),
    ]);
    const texts = workerRows(el).map(r => r.querySelector('.gpu-text')!.textContent!.trim());
    expect(texts).toEqual(['RTX 5090', 'GTX 1080']);
  });

  it('expands a row to its cards panel and collapses it again', () => {
    const el = emit([makeWorker()]);
    expect(el.querySelector('app-worker-cards')).toBeNull();
    const toggle = el.querySelector<HTMLButtonElement>('button.expand-toggle')!;
    expect(toggle.getAttribute('aria-label')).toBe('Show graphics cards of render-01');
    toggle.click();
    fixture.detectChanges();
    expect(el.querySelector('app-worker-cards')).not.toBeNull();
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(toggle.getAttribute('aria-label')).toBe('Hide graphics cards of render-01');
    expect(el.querySelector('#' + toggle.getAttribute('aria-controls'))!.contains(
      el.querySelector('app-worker-cards'))).toBeTrue();
    toggle.click();
    fixture.detectChanges();
    expect(el.querySelector('app-worker-cards')).toBeNull();
  });

  it('links only http(s) worker UI addresses', () => {
    const el = emit([
      makeWorker({ id: 1, ui_url: 'https://10.0.0.5:7443/' }),
      makeWorker({ id: 2, ui_url: HOSTILE_URL }),
    ]);
    const [good, bad] = workerRows(el);
    expect(good.querySelector('a')!.getAttribute('href')).toBe('https://10.0.0.5:7443/');
    expect(good.querySelector('a')!.getAttribute('aria-label')).toBe('Open the worker page of render-01');
    expect(bad.querySelector('a')).toBeNull();
    expect(bad.textContent).toContain('N/A');
  });
});
