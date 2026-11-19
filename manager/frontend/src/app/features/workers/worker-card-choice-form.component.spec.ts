// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { HttpErrorResponse } from '@angular/common/http';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { Subject, throwError } from 'rxjs';
import { STALE_TEXT, WorkerCardChoiceFormComponent } from './worker-card-choice-form.component';
import { WorkerService, Worker } from '../../core/services/worker.service';
import { CardChoiceSendResponse, CardEntry } from '../../core/services/worker-cards.model';
import { HOSTILE_TEXT, makeAlert, makeCard, makeRequest, makeStatus, makeWorker } from './worker-cards.fixtures';

const KEY1 = 'pci:0000:01:00.0';
const KEY2 = 'pci:0000:02:00.0';
const KEY3 = 'pci:0000:03:00.0';

describe('WorkerCardChoiceFormComponent', () => {
  let fixture: ComponentFixture<WorkerCardChoiceFormComponent>;
  let component: WorkerCardChoiceFormComponent;
  let service: jasmine.SpyObj<WorkerService>;
  let el: HTMLElement;

  beforeEach(async () => {
    service = jasmine.createSpyObj('WorkerService', ['sendCardChoice']);
    await TestBed.configureTestingModule({
      imports: [WorkerCardChoiceFormComponent, NoopAnimationsModule],
      providers: [{ provide: WorkerService, useValue: service }],
    }).compileComponents();
  });

  function render(worker: Worker): void {
    fixture = TestBed.createComponent(WorkerCardChoiceFormComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('worker', worker);
    fixture.detectChanges();
    el = fixture.nativeElement as HTMLElement;
  }

  function withCards(cards: CardEntry[], extra: Parameters<typeof makeStatus>[0] = {}): Worker {
    return makeWorker({ card_status: makeStatus({ cards, ...extra }) });
  }

  function boxes(): HTMLInputElement[] {
    return Array.from(el.querySelectorAll<HTMLInputElement>('.card-tick input[type="checkbox"]'));
  }

  function sendButton(): HTMLButtonElement { return el.querySelector<HTMLButtonElement>('button.send')!; }

  function click(input: HTMLElement): void {
    input.click();
    fixture.detectChanges();
  }

  function chooseMethod(value: string): void {
    const select = el.querySelector<HTMLSelectElement>('select.method-select')!;
    select.value = value;
    select.dispatchEvent(new Event('change'));
    fixture.detectChanges();
  }

  it('starts from the reported choice and lists every card that is not lost', () => {
    render(withCards([
      makeCard({ key: KEY1 }), makeCard({ key: KEY2, ticked: false, state: 'unticked', screen: true }),
      makeCard({ key: KEY3, state: 'lost', name: 'Gone card' }),
    ], { method: 'cpu_gpu' }));
    expect(el.querySelector<HTMLSelectElement>('select.method-select')!.value).toBe('cpu_gpu');
    expect(boxes().map(b => b.checked)).toEqual([true, false]);
    expect(el.textContent).not.toContain('Gone card');
    expect(el.querySelectorAll('.choice-card')[1].querySelector('.screen-mark')!.textContent)
      .toContain('Drives a screen');
    expect(el.querySelector('.missing-note')).not.toBeNull();
    expect(el.querySelector('.help')!.textContent).toContain(
      'Unreal renders on a ticked card when it is switched on in the worker\'s settings.');
  });

  it('disables Send while the method includes GPU and nothing is ticked', () => {
    render(withCards([makeCard()], { method: 'gpu' }));
    expect(sendButton().disabled).toBeFalse();
    click(boxes()[0]);
    expect(sendButton().disabled).toBeTrue();
    expect(el.querySelector('.needs-tick')).not.toBeNull();
    chooseMethod('cpu');
    expect(sendButton().disabled).toBeFalse();
    chooseMethod('cpu_gpu');
    expect(sendButton().disabled).toBeTrue();
  });

  it('shows Combine only when the ticked cards are of one family', () => {
    render(withCards([
      makeCard({ key: KEY1, backend: 'CUDA' }),
      makeCard({ key: KEY2, backend: 'OPTIX', ticked: false, state: 'unticked' }),
      makeCard({ key: KEY3, backend: null }),
    ]));
    expect(el.querySelector('.combine')).withContext('one CUDA plus one null backend').toBeNull();
    click(boxes()[1]);
    expect(el.querySelector('.combine')).withContext('CUDA plus OptiX').not.toBeNull();
    click(boxes()[0]);
    expect(el.querySelector('.combine')).withContext('OptiX plus null').toBeNull();
  });

  it('hides Combine under CPU, keeps its value and sends split (C-23)', () => {
    service.sendCardChoice.and.returnValue(new Subject<CardChoiceSendResponse>());
    render(withCards([
      makeCard({ key: KEY1, backend: 'CUDA' }), makeCard({ key: KEY2, backend: 'CUDA' }),
    ], { method: 'cpu', gpu_mode: 'combined' }));
    expect(el.querySelector('.combine')).toBeNull();
    chooseMethod('cpu_gpu');
    expect(el.querySelector<HTMLInputElement>('.combine input')!.checked).toBeTrue();
    chooseMethod('cpu');
    click(sendButton());
    expect(service.sendCardChoice.calls.mostRecent().args[1].gpu_mode).toBe('split');
  });

  it('links "Dismiss its alert first" to its checkbox and says a Send replaces a waiting change', () => {
    render(makeWorker({ card_choice_request: makeRequest(), card_status: makeStatus({ cards: [
      makeCard({ key: KEY1 }), makeCard({ key: KEY2, ticked: false, state: 'held' }),
    ] }) }));
    const note = el.querySelector<HTMLElement>('.held-note')!;
    expect(note.id).toBeTruthy();
    expect(boxes()[1].getAttribute('aria-describedby')).toBe(note.id);
    expect(boxes()[0].hasAttribute('aria-describedby')).toBeFalse();
    expect(el.querySelector('.replaces-note')!.textContent).toContain('Sending replaces the change that is waiting.');
    render(withCards([makeCard()]));
    expect(el.querySelector('.replaces-note')).toBeNull();
  });

  it('disables a held unticked card with "Dismiss its alert first" and keeps a held ticked one', () => {
    render(withCards([
      makeCard({ key: KEY1, ticked: false, state: 'unticked' }),
      makeCard({ key: KEY2, ticked: false, state: 'held' }),
      makeCard({ key: KEY3, ticked: true, state: 'held' }),
    ], { alerts: [makeAlert({ kind: 'card_lost_blender', key: KEY1 })] }));
    expect(boxes().map(b => b.disabled)).toEqual([true, true, false]);
    const notes = Array.from(el.querySelectorAll('.choice-card'))
      .map(li => li.querySelector('.held-note')?.textContent?.trim() ?? '');
    expect(notes).toEqual(['Dismiss its alert first', 'Dismiss its alert first', '']);
  });

  it('builds the body from the snapshot and sends split with one ticked card', () => {
    service.sendCardChoice.and.returnValue(new Subject<CardChoiceSendResponse>());
    render(withCards([
      makeCard({ key: KEY1, backend: 'CUDA' }), makeCard({ key: KEY2, backend: 'CUDA' }),
    ], { gpu_mode: 'combined', revision: 6 }));
    click(boxes()[1]);
    // A poll changes the live cards but not the revision: the snapshot decides.
    fixture.componentRef.setInput('worker', withCards([makeCard({ key: KEY1, device_id: 1 })], { revision: 6 }));
    fixture.detectChanges();
    click(sendButton());
    expect(service.sendCardChoice).toHaveBeenCalledOnceWith(1, {
      method: 'gpu', gpu_mode: 'split',
      cards: [{ key: KEY1, vendor_id: 0x10de, device_id: 0x2b85 }],
      shown: [KEY1, KEY2], base_revision: 6,
    });
  });

  it('sends combined with two ticked cards of one family', () => {
    service.sendCardChoice.and.returnValue(new Subject<CardChoiceSendResponse>());
    render(withCards([
      makeCard({ key: KEY1, backend: 'CUDA' }), makeCard({ key: KEY2, backend: 'OPTIX' }),
    ], { gpu_mode: 'combined' }));
    expect(el.querySelector<HTMLInputElement>('.combine input')!.checked).toBeTrue();
    click(sendButton());
    expect(service.sendCardChoice.calls.mostRecent().args[1].gpu_mode).toBe('combined');
  });

  it('disables Send in flight and emits the 202 body', () => {
    const answer = new Subject<CardChoiceSendResponse>();
    service.sendCardChoice.and.returnValue(answer.asObservable());
    render(withCards([makeCard()]));
    const sent: CardChoiceSendResponse[] = [];
    component.sent.subscribe(b => sent.push(b));
    click(sendButton());
    expect(sendButton().disabled).toBeTrue();
    expect(el.querySelector<HTMLButtonElement>('button.cancel')!.disabled).toBeTrue();
    click(sendButton());
    expect(service.sendCardChoice).toHaveBeenCalledTimes(1);
    const body = { card_choice_request: makeRequest(), replaced: null };
    answer.next(body);
    answer.complete();
    fixture.detectChanges();
    expect(sent).toEqual([body]);
    expect(component.sending).toBeFalse();
    expect(el.querySelector<HTMLButtonElement>('button.cancel')!.disabled).toBeFalse();
  });

  it('blocks Send after a new revision until "Start again" takes a new snapshot', () => {
    render(withCards([makeCard({ key: KEY1 })], { revision: 3 }));
    expect(el.querySelector('.stale')).toBeNull();
    fixture.componentRef.setInput('worker', withCards([
      makeCard({ key: KEY1, ticked: false, state: 'unticked' }), makeCard({ key: KEY2 }),
    ], { revision: 4, method: 'cpu' }));
    fixture.detectChanges();
    expect(el.querySelector('.stale')!.textContent).toContain(STALE_TEXT);
    expect(sendButton().disabled).toBeTrue();
    expect(boxes().length).toBe(1);

    click(el.querySelector<HTMLButtonElement>('button.start-again')!);
    expect(el.querySelector('.stale')).toBeNull();
    expect(sendButton().disabled).toBeFalse();
    expect(boxes().map(b => b.checked)).toEqual([false, true]);
    expect(component.snapshot.revision).toBe(4);
    expect(el.querySelector<HTMLSelectElement>('select.method-select')!.value).toBe('cpu');
  });

  it('shows the detail of an error answer, else the HTTP status (R6)', () => {
    service.sendCardChoice.and.returnValue(throwError(() => new HttpErrorResponse({
      status: 409, error: { detail: 'No change is allowed.' },
    })));
    render(withCards([makeCard()]));
    click(sendButton());
    expect(el.querySelector('.action-error')!.textContent).toContain('No change is allowed.');
    expect(sendButton().disabled).toBeFalse();

    service.sendCardChoice.and.returnValue(throwError(() => new HttpErrorResponse({ status: 502 })));
    click(sendButton());
    expect(el.querySelector('.action-error')!.textContent).toContain('The request failed (HTTP 502).');
  });

  it('emits cancelled from Cancel', () => {
    render(withCards([makeCard()]));
    let cancelled = 0;
    component.cancelled.subscribe(() => cancelled++);
    click(el.querySelector<HTMLButtonElement>('button.cancel')!);
    expect(cancelled).toBe(1);
  });

  it('keeps a card name with markup as text', () => {
    render(withCards([makeCard({ name: '<img src=x onerror=alert(1)>' }), makeCard({ key: KEY2, name: HOSTILE_TEXT })]));
    expect(el.querySelector('img')).toBeNull();
    expect(el.querySelectorAll('.card-tick')[0].textContent).toContain('<img src=x onerror=alert(1)>');
    expect(el.querySelectorAll('.card-tick')[1].textContent).toContain(HOSTILE_TEXT);
  });
});
