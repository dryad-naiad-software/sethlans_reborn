// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { HttpErrorResponse } from '@angular/common/http';
import { By } from '@angular/platform-browser';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { Subject, of, throwError } from 'rxjs';
import { WITHDRAWN_TEXT, WorkerCardChoiceStatusComponent } from './worker-card-choice-status.component';
import { WorkerCardChoiceFormComponent } from './worker-card-choice-form.component';
import { WorkerService, Worker } from '../../core/services/worker.service';
import { CardChoiceOutcome, CardStatus } from '../../core/services/worker-cards.model';
import { WorkerCardPatch } from './worker-row.util';
import {
  READ_ONLY_ENV, READ_ONLY_NO_STATUS, READ_ONLY_NOT_ALLOWED,
} from './worker-card-choice.util';
import { HOSTILE_TEXT, makeOutcome, makeRequest, makeStatus, makeWorker } from './worker-cards.fixtures';

describe('WorkerCardChoiceStatusComponent', () => {
  let fixture: ComponentFixture<WorkerCardChoiceStatusComponent>;
  let component: WorkerCardChoiceStatusComponent;
  let service: jasmine.SpyObj<WorkerService>;
  let patches: WorkerCardPatch[];
  let el: HTMLElement;

  beforeEach(async () => {
    service = jasmine.createSpyObj('WorkerService', ['sendCardChoice', 'withdrawCardChoice']);
    await TestBed.configureTestingModule({
      imports: [WorkerCardChoiceStatusComponent, NoopAnimationsModule],
      providers: [{ provide: WorkerService, useValue: service }],
    }).compileComponents();
  });

  afterEach(() => jasmine.clock().uninstall());

  function render(worker: Worker): void {
    fixture = TestBed.createComponent(WorkerCardChoiceStatusComponent);
    component = fixture.componentInstance;
    patches = [];
    component.choiceUpdated.subscribe(p => patches.push(p));
    fixture.componentRef.setInput('worker', worker);
    fixture.detectChanges();
    el = fixture.nativeElement as HTMLElement;
  }

  function text(selector: string): string {
    return el.querySelector(selector)?.textContent?.trim() ?? '';
  }

  function click(selector: string): void {
    el.querySelector<HTMLButtonElement>(selector)!.click();
    fixture.detectChanges();
  }

  it('shows the pending change with its age and a Withdraw button', () => {
    jasmine.clock().install();
    jasmine.clock().mockDate(new Date('2026-11-19T10:05:30Z'));
    render(makeWorker({ card_choice_request: makeRequest({ requested_at: '2026-11-19T10:00:00Z' }) }));
    expect(text('.pending')).toBe('Change sent 5 minutes ago, waiting for the worker.');
    expect(el.querySelector('.offline')).toBeNull();
    expect(el.querySelector('button.withdraw')).not.toBeNull();
  });

  it('adds the offline note when the worker is OFFLINE', () => {
    render(makeWorker({ status: 'OFFLINE', card_choice_request: makeRequest() }));
    expect(text('.offline')).toBe('The worker is offline; the change waits for its next heartbeat.');
  });

  it('shows nothing pending for null and {}', () => {
    for (const request of [null, {} as Worker['card_choice_request']]) {
      render(makeWorker({ card_choice_request: request }));
      expect(el.querySelector('.pending')).toBeNull();
      expect(el.querySelector('button.withdraw')).toBeNull();
    }
  });

  it('withdraws, disables Withdraw in flight, clears the row at once and says so', () => {
    const answer = new Subject<void>();
    service.withdrawCardChoice.and.returnValue(answer.asObservable());
    render(makeWorker({ id: 4, card_choice_request: makeRequest() }));
    click('button.withdraw');
    expect(service.withdrawCardChoice).toHaveBeenCalledOnceWith(4);
    expect(el.querySelector<HTMLButtonElement>('button.withdraw')!.disabled).toBeTrue();
    click('button.withdraw');
    expect(service.withdrawCardChoice).toHaveBeenCalledTimes(1);

    answer.next();
    answer.complete();
    expect(patches).toEqual([{ card_choice_request: null }]);
    fixture.componentRef.setInput('worker', makeWorker({ id: 4, card_choice_request: null }));
    fixture.detectChanges();
    expect(text('.withdrawn')).toBe(WITHDRAWN_TEXT);
    expect(el.querySelector('.pending')).toBeNull();
  });

  it('shows a withdraw error as its detail, else the HTTP status', () => {
    service.withdrawCardChoice.and.returnValue(throwError(() => new HttpErrorResponse({
      status: 409, error: { detail: 'No change is waiting for this worker.' },
    })));
    render(makeWorker({ card_choice_request: makeRequest() }));
    click('button.withdraw');
    expect(text('.action-error')).toBe('No change is waiting for this worker.');
    expect(el.querySelector<HTMLButtonElement>('button.withdraw')!.disabled).toBeFalse();

    service.withdrawCardChoice.and.returnValue(throwError(() => new HttpErrorResponse({ status: 500 })));
    click('button.withdraw');
    expect(text('.action-error')).toBe('The request failed (HTTP 500).');
    expect(patches).toEqual([]);
  });

  it('says when a change was applied and who requested it', () => {
    render(makeWorker({ card_choice_outcome: makeOutcome() }));
    expect(text('.outcome')).toMatch(/^Applied at .+$/);
    expect(text('.outcome')).not.toContain(': ');
    expect(text('.requested')).toMatch(/^Requested by user id=5 at .+\.$/);
  });

  it('says when a change was refused, with its reason as text', () => {
    render(makeWorker({ card_choice_outcome: makeOutcome({
      result: 'refused', code: 'card_held', reason: HOSTILE_TEXT,
    }) }));
    expect(text('.outcome')).toMatch(/^Refused at .+: /);
    expect(text('.outcome').endsWith(HOSTILE_TEXT)).toBeTrue();
    expect(HOSTILE_TEXT).toContain('<img src=x onerror=alert(1)>');
    expect(el.querySelector('.outcome')!.classList).toContain('refused');
    expect(el.querySelector('img')).toBeNull();
  });

  it('shows no outcome for null and {} and no time for a bad one', () => {
    for (const outcome of [null, {} as CardChoiceOutcome]) {
      render(makeWorker({ card_choice_outcome: outcome }));
      expect(el.querySelector('.outcome')).toBeNull();
    }
    render(makeWorker({ card_choice_outcome: makeOutcome({ finished_at: 'bad', requested_by: undefined }) }));
    expect(text('.outcome')).toBe('Applied');
    expect(el.querySelector('.requested')).toBeNull();
  });

  describe('read-only states (FR-CF-7)', () => {
    const cases: [string, CardStatus | null, string][] = [
      ['env override', makeStatus({ env_override: true }), READ_ONLY_ENV],
      ['switch off', makeStatus({ manager_changes_allowed: false }), READ_ONLY_NOT_ALLOWED],
      ['no status', null, READ_ONLY_NO_STATUS],
      ['empty status', {} as CardStatus, READ_ONLY_NO_STATUS],
    ];
    for (const [name, status, sentence] of cases) {
      it(`shows one sentence and no Change button: ${name}`, () => {
        render(makeWorker({ card_status: status }));
        expect(el.querySelectorAll('.read-only').length).toBe(1);
        expect(text('.read-only')).toBe(sentence);
        expect(el.querySelector('button.change')).toBeNull();
      });
    }

    it('closes an open form when the worker turns read-only', () => {
      render(makeWorker());
      click('button.change');
      expect(fixture.debugElement.query(By.directive(WorkerCardChoiceFormComponent)))
        .not.toBeNull();
      fixture.componentRef.setInput('worker', makeWorker({ card_status: makeStatus({ env_override: true }) }));
      fixture.detectChanges();
      expect(el.querySelector('app-worker-card-choice-form')).toBeNull();
      expect(text('.read-only')).toBe(READ_ONLY_ENV);
    });
  });

  it('opens the form from Change and closes it on Cancel', () => {
    render(makeWorker());
    expect(el.querySelector('.read-only')).toBeNull();
    click('button.change');
    expect(el.querySelector('app-worker-card-choice-form')).not.toBeNull();
    expect(el.querySelector('button.change')).toBeNull();
    click('app-worker-card-choice-form button.cancel');
    expect(el.querySelector('app-worker-card-choice-form')).toBeNull();
    expect(el.querySelector('button.change')).not.toBeNull();
  });

  describe('focus after a button removes itself (B2)', () => {
    /** Acts as the list does: applies each patch to the worker input. */
    function renderWithParent(worker: Worker): void {
      render(worker);
      component.choiceUpdated.subscribe(p => {
        fixture.componentRef.setInput('worker', { ...component.worker, ...p });
      });
    }

    function focused(): Element | null { return document.activeElement; }

    it('Change focuses the method select and Cancel the Change button', () => {
      renderWithParent(makeWorker());
      click('button.change');
      expect(focused()).toBe(el.querySelector('select.method-select'));
      click('app-worker-card-choice-form button.cancel');
      expect(focused()).toBe(el.querySelector('button.change'));
    });

    it('Send focuses the pending line', () => {
      service.sendCardChoice.and.returnValue(of({ card_choice_request: makeRequest(), replaced: null }));
      renderWithParent(makeWorker());
      click('button.change');
      click('app-worker-card-choice-form button.send');
      fixture.detectChanges();
      expect(focused()).toBe(el.querySelector('p.pending'));
      expect(el.querySelector('p.pending')!.getAttribute('tabindex')).toBe('-1');
    });

    it('Withdraw focuses the withdrawn line', () => {
      service.withdrawCardChoice.and.returnValue(of(undefined));
      renderWithParent(makeWorker({ card_choice_request: makeRequest() }));
      click('button.withdraw');
      fixture.detectChanges();
      expect(focused()).toBe(el.querySelector('p.withdrawn'));
      expect(el.querySelector('p.withdrawn')!.getAttribute('tabindex')).toBe('-1');
    });
  });

  it('ends the "Withdrawn" line when a new request or a new outcome arrives', () => {
    service.withdrawCardChoice.and.returnValue(of(undefined));
    const outcome = makeOutcome();
    render(makeWorker({ card_choice_request: makeRequest(), card_choice_outcome: outcome }));
    click('button.withdraw');
    const set = (w: Worker): void => { fixture.componentRef.setInput('worker', w); fixture.detectChanges(); };
    // A poll that started before the Withdraw lands after it, still carrying the request.
    set(makeWorker({ card_choice_request: makeRequest(), card_choice_outcome: outcome }));
    expect(text('.withdrawn')).toBe(WITHDRAWN_TEXT);
    expect(el.querySelector('.pending')).toBeNull();
    set(makeWorker({ card_choice_outcome: outcome }));
    expect(el.querySelector('.withdrawn')).not.toBeNull();
    set(makeWorker({ card_choice_outcome: makeOutcome({ id: 'f'.repeat(32) }) }));
    expect(el.querySelector('.withdrawn')).toBeNull();
    set(makeWorker({ card_choice_request: makeRequest() }));
    click('button.withdraw');
    set(makeWorker({ card_choice_request: makeRequest({ id: '9'.repeat(32) }) }));
    expect(el.querySelector('.pending')).not.toBeNull();
    set(makeWorker());
    expect(el.querySelector('.withdrawn')).toBeNull();
  });

  it('applies the 202 body at once and closes the form after Send', () => {
    const request = makeRequest();
    service.sendCardChoice.and.returnValue(of({ card_choice_request: request, replaced: null }));
    render(makeWorker());
    click('button.change');
    click('app-worker-card-choice-form button.send');
    expect(service.sendCardChoice).toHaveBeenCalledTimes(1);
    expect(patches).toEqual([{ card_choice_request: request }]);
    expect(el.querySelector('app-worker-card-choice-form')).toBeNull();
  });
});
