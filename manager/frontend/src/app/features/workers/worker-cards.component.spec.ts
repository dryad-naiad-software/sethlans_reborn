// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { WorkerCardsComponent } from './worker-cards.component';
import { WorkerService, Worker } from '../../core/services/worker.service';
import { CardEntry, CardStatus } from '../../core/services/worker-cards.model';
import {
  HOSTILE_TEXT, HOSTILE_URL, makeAlert, makeCard, makeStatus, makeWorker,
} from './worker-cards.fixtures';

const NOT_REPORTED = 'This worker has not reported its graphics cards yet.';

describe('WorkerCardsComponent', () => {
  let fixture: ComponentFixture<WorkerCardsComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [WorkerCardsComponent],
      providers: [{ provide: WorkerService, useValue: jasmine.createSpyObj('WorkerService', ['dismissCardAlert']) }],
    }).compileComponents();
  });

  function render(worker: Worker, uiUrl: string | null = null): HTMLElement {
    fixture = TestBed.createComponent(WorkerCardsComponent);
    fixture.componentRef.setInput('worker', worker);
    fixture.componentRef.setInput('uiUrl', uiUrl);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('says the cards are not reported for null and for {}', () => {
    for (const status of [null, {} as CardStatus, makeStatus({ schema: 2 })]) {
      const el = render(makeWorker({ card_status: status }));
      expect(el.querySelector('.no-status')!.textContent).toContain(NOT_REPORTED);
      expect(el.querySelector('table.cards')).toBeNull();
    }
  });

  it('shows each card with name, address, tick, state and note', () => {
    const el = render(makeWorker({ card_status: makeStatus({ cards: [
      makeCard({ note: 'Blender not checked yet' }),
      makeCard({ key: 'pci:0000:02:00.0', name: 'RX 7900 XT', ticked: false, state: 'unticked' }),
      makeCard({ key: 'pci:0000:03:00.0', name: 'RX 7900 XTX', state: 'lost' }),
    ] }) }));
    const rows = el.querySelectorAll('tr.card');
    expect(rows.length).toBe(3);
    expect(rows[0].querySelector('.card-name')!.textContent).toContain('NVIDIA GeForce RTX 5090');
    expect(rows[0].querySelector('.card-address')!.textContent).toContain('PCI 01:00.0');
    expect(rows[0].querySelector('.card-ticked')!.textContent).toContain('Yes');
    expect(rows[0].querySelector('.card-note')!.textContent).toContain('Blender not checked yet');
    expect(rows[1].querySelector('.card-ticked')!.textContent).toContain('No');
    expect(rows[1].querySelector('.card-state')!.textContent).toContain('Not ticked');
    expect(rows[2].querySelector('.card-state')!.textContent).toContain('Missing');
    expect(rows[2].querySelector('.card-state')!.classList).toContain('tone-bad');
  });

  it('shows the screen marks of each card (FR-CS-4)', () => {
    const { screen, primary_screen, ...absent } = makeCard({ key: 'pci:0000:05:00.0' });
    const el = render(makeWorker({ card_status: makeStatus({ cards: [
      makeCard({ screen: true, primary_screen: true }),
      makeCard({ key: 'pci:0000:02:00.0', screen: true, primary_screen: false }),
      makeCard({ key: 'pci:0000:03:00.0', screen: true, primary_screen: null }),
      makeCard({ key: 'pci:0000:04:00.0', screen: null, primary_screen: null }),
      absent as CardEntry,
      makeCard({ key: 'pci:0000:06:00.0', screen: false, primary_screen: false }),
    ] }) }));
    expect(screen).toBeNull();
    expect(primary_screen).toBeNull();
    const marks = Array.from(el.querySelectorAll('tr.card .card-screen')).map(td =>
      Array.from(td.querySelectorAll('.screen-mark')).map(s => s.textContent!.trim()));
    expect(marks).toEqual([
      ['Drives a screen', 'Main screen'], ['Drives a screen'], ['Drives a screen'],
      ['Screen: unknown'], ['Screen: unknown'], [],
    ]);
    const cells = el.querySelectorAll('tr.card .card-screen');
    expect(cells[0].textContent!.trim()).toBe('Drives a screen, Main screen');
    expect(cells[1].textContent!.trim()).toBe('Drives a screen');
    expect(cells[5].textContent!.trim()).toBe('');
  });

  it('shows the screen warning from the worker once and adds none of its own', () => {
    const text = 'NVIDIA GeForce RTX 5090 (PCI 01:00.0) is ticked and drives the main screen; rendering can slow it.';
    const el = render(makeWorker({ card_status: makeStatus({
      cards: [makeCard({ screen: true, primary_screen: true })],
      warnings: [{ code: 'ticked_drives_screen', key: 'pci:0000:01:00.0', text }],
    }) }));
    const warnings = el.querySelectorAll('.warning');
    expect(warnings.length).toBe(1);
    expect(warnings[0].textContent).toContain(text);
    expect(el.textContent!.split('is ticked and drives').length - 1).toBe(1);
    expect(render(makeWorker({ card_status: makeStatus({
      cards: [makeCard({ screen: true, primary_screen: true })],
    }) })).querySelector('.warning')).toBeNull();
  });

  it('shows the problem, warnings, notices and the rebuild wait', () => {
    const el = render(makeWorker({ card_status: makeStatus({
      problem: 'None of the ticked graphics cards can be used.',
      rebuild_waiting: true,
      warnings: [{ code: 'engine_not_tied', key: null, text: 'Eevee may use another card.' }],
      notices: [{ kind: 'card_new', key: 'pci:0000:04:00.0', name: 'RX 7800 XT' }],
    }) }));
    expect(el.querySelector('.problem')!.textContent).toContain('None of the ticked graphics cards can be used.');
    expect(el.querySelector('.warning')!.textContent).toContain('Eevee may use another card.');
    expect(el.querySelector('.notice')!.textContent).toContain('New card found: RX 7800 XT');
    expect(el.querySelector('.rebuild-waiting')!.textContent)
      .toContain('Waiting for running GPU jobs to finish before using the new card list.');
  });

  it('hides the problem and the rebuild wait when absent', () => {
    const el = render(makeWorker());
    expect(el.querySelector('.problem')).toBeNull();
    expect(el.querySelector('.rebuild-waiting')).toBeNull();
  });

  it('passes the alerts and dismissals to the alerts child', () => {
    const alert = makeAlert();
    const el = render(makeWorker({
      card_status: makeStatus({ alerts: [alert] }), card_alert_dismissals: [alert.id],
    }));
    expect(el.querySelector('app-worker-card-alerts .alert')).not.toBeNull();
    expect(el.querySelector('.dismissal-sent')).not.toBeNull();
  });

  it('links to the worker page only from the given uiUrl', () => {
    const el = render(makeWorker(), 'https://10.0.0.5:7443/');
    const links = el.querySelectorAll('a');
    expect(links.length).toBe(1);
    expect(links[0].getAttribute('href')).toBe('https://10.0.0.5:7443/');
    expect(render(makeWorker(), null).querySelector('a')).toBeNull();
  });

  it('keeps hostile card text as text and builds no link from it', () => {
    const el = render(makeWorker({ card_status: makeStatus({
      problem: HOSTILE_TEXT,
      cards: [makeCard({ name: HOSTILE_TEXT, note: HOSTILE_URL })],
      warnings: [{ code: 'x', key: null, text: HOSTILE_TEXT }],
      notices: [{ kind: 'card_gone', key: 'pci:0000:05:00.0', name: HOSTILE_URL }],
      alerts: [makeAlert({ name: HOSTILE_TEXT, detail: HOSTILE_URL })],
    }) }));
    expect(el.querySelector('img')).toBeNull();
    expect(el.querySelector('a')).toBeNull();
    expect(el.querySelector('.card-name')!.textContent).toContain(HOSTILE_TEXT);
    expect(el.querySelector('.problem')!.textContent).toContain(HOSTILE_TEXT);
    expect(el.innerHTML).not.toContain('href="javascript');
  });
});
