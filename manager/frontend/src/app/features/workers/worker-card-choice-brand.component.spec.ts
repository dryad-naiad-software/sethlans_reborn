// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { HttpErrorResponse } from '@angular/common/http';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { throwError } from 'rxjs';
import { WorkerCardChoiceFormComponent } from './worker-card-choice-form.component';
import { WorkerService } from '../../core/services/worker.service';
import { CardEntry } from '../../core/services/worker-cards.model';
import { makeCard, makeStatus, makeWorker } from './worker-cards.fixtures';

const KEY1 = 'pci:0000:01:00.0';
const KEY2 = 'pci:0000:02:00.0';
const KEY3 = 'pci:0000:03:00.0';
const MIXED_BRANDS_TEXT = 'Graphics cards of more than one brand (AMD, NVIDIA) cannot be ticked together on one '
  + 'machine; this is not supported. Tick the cards of one brand only.';

/** The card-change form's brand rule (D7, FR-PC-BR-5). */
describe('WorkerCardChoiceFormComponent brand rule', () => {
  let fixture: ComponentFixture<WorkerCardChoiceFormComponent>;
  let service: jasmine.SpyObj<WorkerService>;
  let el: HTMLElement;

  beforeEach(async () => {
    service = jasmine.createSpyObj('WorkerService', ['sendCardChoice']);
    await TestBed.configureTestingModule({
      imports: [WorkerCardChoiceFormComponent, NoopAnimationsModule],
      providers: [{ provide: WorkerService, useValue: service }],
    }).compileComponents();
  });

  function render(cards: CardEntry[]): void {
    fixture = TestBed.createComponent(WorkerCardChoiceFormComponent);
    fixture.componentRef.setInput('worker', makeWorker({ card_status: makeStatus({ cards }) }));
    fixture.detectChanges();
    el = fixture.nativeElement as HTMLElement;
  }

  function boxes(): HTMLInputElement[] {
    return Array.from(el.querySelectorAll<HTMLInputElement>('.card-tick input[type="checkbox"]'));
  }

  function notes(): string[] {
    return Array.from(el.querySelectorAll('.choice-card'))
      .map(li => li.querySelector('.held-note')?.textContent?.trim() ?? '');
  }

  function click(input: HTMLElement): void {
    input.click();
    fixture.detectChanges();
  }

  const nvidia = (key: string, ticked: boolean): CardEntry =>
    makeCard({ key, ticked, state: ticked ? 'ok' : 'unticked' });
  const amd = (key: string, ticked: boolean): CardEntry => makeCard({
    key, ticked, state: ticked ? 'ok' : 'unticked', name: 'AMD Radeon RX 7900 XTX', vendor_id: 0x1002, backend: 'HIP',
  });

  it('disables a card of another brand with the note and links it to its checkbox', () => {
    render([nvidia(KEY1, true), nvidia(KEY2, false), amd(KEY3, false)]);
    expect(boxes().map(b => b.disabled)).toEqual([false, false, true]);
    expect(notes()).toEqual(['', '', 'Not supported together with NVIDIA cards.']);
    const note = el.querySelectorAll<HTMLElement>('.choice-card')[2].querySelector<HTMLElement>('.held-note')!;
    expect(boxes()[2].getAttribute('aria-describedby')).toBe(note.id);
  });

  it('frees the other brand once the ticked brand is unticked, then blocks the first brand', () => {
    render([nvidia(KEY1, true), amd(KEY2, false)]);
    click(boxes()[0]);
    expect(boxes().map(b => b.disabled)).toEqual([false, false]);
    expect(notes()).toEqual(['', '']);
    click(boxes()[1]);
    expect(boxes().map(b => b.disabled)).toEqual([true, false]);
    expect(notes()).toEqual(['Not supported together with AMD cards.', '']);
  });

  it('blocks every other card beside a ticked card of unknown brand', () => {
    render([makeCard({ key: KEY1, vendor_id: null, backend: null }), nvidia(KEY2, false)]);
    expect(boxes().map(b => b.disabled)).toEqual([false, true]);
    expect(notes()).toEqual(['', 'Not supported together with other cards.']);
  });

  it('shows the manager\'s mixed-brands sentence from a 400 answer', () => {
    service.sendCardChoice.and.returnValue(throwError(() => new HttpErrorResponse({
      status: 400, error: { detail: MIXED_BRANDS_TEXT },
    })));
    render([nvidia(KEY1, true), amd(KEY2, true)]);
    expect(boxes().map(b => b.disabled)).toEqual([false, false]);
    click(el.querySelector<HTMLButtonElement>('button.send')!);
    expect(el.querySelector('.action-error')!.textContent).toContain(MIXED_BRANDS_TEXT);
  });
});
