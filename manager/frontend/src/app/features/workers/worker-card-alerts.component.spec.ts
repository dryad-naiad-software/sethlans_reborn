// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { HttpErrorResponse } from '@angular/common/http';
import { Subject, of, throwError } from 'rxjs';
import { WorkerCardAlertsComponent } from './worker-card-alerts.component';
import { WorkerService } from '../../core/services/worker.service';
import { CardAlert, DismissCardAlertResponse } from '../../core/services/worker-cards.model';
import { HOSTILE_TEXT, makeAlert, makeStatus } from './worker-cards.fixtures';

describe('WorkerCardAlertsComponent', () => {
  let fixture: ComponentFixture<WorkerCardAlertsComponent>;
  let component: WorkerCardAlertsComponent;
  let service: jasmine.SpyObj<WorkerService>;

  beforeEach(async () => {
    service = jasmine.createSpyObj('WorkerService', ['dismissCardAlert']);
    await TestBed.configureTestingModule({
      imports: [WorkerCardAlertsComponent],
      providers: [{ provide: WorkerService, useValue: service }],
    }).compileComponents();
  });

  function render(alerts: CardAlert[], dismissals: string[] = []): HTMLElement {
    fixture = TestBed.createComponent(WorkerCardAlertsComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('workerId', 4);
    fixture.componentRef.setInput('alerts', alerts);
    fixture.componentRef.setInput('dismissals', dismissals);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  function dismissButton(el: HTMLElement): HTMLButtonElement | null {
    return el.querySelector('button.dismiss');
  }

  it('renders nothing without alerts', () => {
    expect(render([]).querySelector('.alerts')).toBeNull();
  });

  it('shows the alert with its detail and time', () => {
    const el = render([makeAlert()]);
    const text = el.querySelector('.alert')!.textContent!;
    expect(text).toContain('Card missing');
    expect(text).toContain('NVIDIA GeForce RTX 5090');
    expect(text).toContain('missing from the card list');
    expect(el.querySelector('.alert-time')).not.toBeNull();
  });

  it('shows no time when raised_at is null', () => {
    const el = render([makeAlert({ raised_at: null })]);
    expect(el.querySelector('.alert-time')).toBeNull();
  });

  it('shows "Dismissal sent" instead of the button for a dismissed id', () => {
    const alert = makeAlert();
    const el = render([alert], [alert.id]);
    expect(dismissButton(el)).toBeNull();
    expect(el.querySelector('.dismissal-sent')!.textContent).toContain('Dismissal sent');
    expect(el.querySelector('.dismissal-sent')!.getAttribute('role')).toBe('status');
  });

  describe('focus after a dismissal', () => {
    const first = makeAlert();
    const second = makeAlert({ id: 'c'.repeat(32), key: 'pci:0000:02:00.0' });

    /** Acts as the list does: applies the 200 body's dismissals to the input. */
    function dismissWithParent(el: HTMLElement, index: number): void {
      service.dismissCardAlert.and.callFake((_w: number, id: string) => of({
        card_status: makeStatus({ alerts: [first, second] }),
        card_alert_dismissals: [...component.dismissals, id],
      }));
      component.dismissed.subscribe(body => {
        fixture.componentRef.setInput('dismissals', body.card_alert_dismissals);
      });
      const button = el.querySelectorAll<HTMLButtonElement>('button.dismiss')[index];
      button.focus();
      button.click();
      fixture.detectChanges();
    }

    it('moves to the next Dismiss button', () => {
      const el = render([first, second]);
      dismissWithParent(el, 0);
      expect(document.activeElement).toBe(el.querySelector('button.dismiss'));
      expect((document.activeElement as HTMLElement).dataset['alertIndex']).toBe('1');
    });

    it('moves to the alerts list when no Dismiss button is left', () => {
      const el = render([first, second], [first.id]);
      dismissWithParent(el, 0);
      expect(el.querySelector('button.dismiss')).toBeNull();
      expect(document.activeElement).toBe(el.querySelector('ul.alerts'));
    });
  });

  it('sends the dismissal, disables the button in flight and emits the 200 body', () => {
    const alert = makeAlert();
    const answer = new Subject<DismissCardAlertResponse>();
    service.dismissCardAlert.and.returnValue(answer.asObservable());
    const el = render([alert]);
    const emitted: DismissCardAlertResponse[] = [];
    component.dismissed.subscribe(b => emitted.push(b));

    dismissButton(el)!.click();
    fixture.detectChanges();
    expect(service.dismissCardAlert).toHaveBeenCalledOnceWith(4, alert.id);
    expect(dismissButton(el)!.disabled).toBeTrue();

    dismissButton(el)!.click();
    expect(service.dismissCardAlert).toHaveBeenCalledTimes(1);

    const body = { card_status: makeStatus({ alerts: [alert] }), card_alert_dismissals: [alert.id] };
    answer.next(body);
    answer.complete();
    fixture.detectChanges();
    expect(emitted).toEqual([body]);
    expect(component.inFlight.size).toBe(0);
  });

  it('shows the detail of an error answer', () => {
    service.dismissCardAlert.and.returnValue(throwError(() => new HttpErrorResponse({
      status: 400, error: { detail: 'This alert is not open on the worker.' },
    })));
    const el = render([makeAlert()]);
    dismissButton(el)!.click();
    fixture.detectChanges();
    expect(el.querySelector('.action-error')!.textContent).toContain('This alert is not open on the worker.');
    expect(dismissButton(el)!.disabled).toBeFalse();
  });

  it('falls back to the HTTP status without a detail', () => {
    service.dismissCardAlert.and.returnValue(throwError(() => new HttpErrorResponse({ status: 503 })));
    const el = render([makeAlert()]);
    dismissButton(el)!.click();
    fixture.detectChanges();
    expect(el.querySelector('.action-error')!.textContent).toContain('The request failed (HTTP 503).');
  });

  it('keeps hostile alert text as text', () => {
    const el = render([makeAlert({ name: HOSTILE_TEXT, detail: HOSTILE_TEXT })]);
    expect(el.querySelector('img')).toBeNull();
    expect(el.querySelector('.alert-card')!.textContent).toContain(HOSTILE_TEXT);
  });
});
