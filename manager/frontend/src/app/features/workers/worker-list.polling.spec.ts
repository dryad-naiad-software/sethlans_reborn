// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { Subject, of } from 'rxjs';
import { WorkerListComponent } from './worker-list.component';
import { WorkerCardsComponent } from './worker-cards.component';
import { WorkerCardAlertsComponent } from './worker-card-alerts.component';
import { STALE_TEXT, WorkerCardChoiceFormComponent } from './worker-card-choice-form.component';
import { WorkerService, Worker } from '../../core/services/worker.service';
import { DismissCardAlertResponse } from '../../core/services/worker-cards.model';
import { makeAlert, makeCard, makeRequest, makeStatus, makeWorker } from './worker-cards.fixtures';

/** R1: an expanded row and its panel survive a poll that returns new objects. */
describe('WorkerListComponent polling', () => {
  let fixture: ComponentFixture<WorkerListComponent>;
  let list$: Subject<Worker[]>;
  let service: jasmine.SpyObj<WorkerService>;

  beforeEach(async () => {
    list$ = new Subject<Worker[]>();
    service = jasmine.createSpyObj('WorkerService', [
      'pollList', 'dismissCardAlert', 'sendCardChoice', 'withdrawCardChoice',
    ]);
    service.pollList.and.returnValue(list$.asObservable());
    await TestBed.configureTestingModule({
      imports: [WorkerListComponent, NoopAnimationsModule],
      providers: [{ provide: WorkerService, useValue: service }],
    }).compileComponents();
    fixture = TestBed.createComponent(WorkerListComponent);
    fixture.detectChanges();
  });

  function poll(workers: Worker[]): void {
    list$.next(workers);
    fixture.detectChanges();
  }

  function panels(): WorkerCardsComponent[] {
    return fixture.debugElement.queryAll(By.directive(WorkerCardsComponent))
      .map(d => d.componentInstance as WorkerCardsComponent);
  }

  function expand(index: number): void {
    const toggles = fixture.nativeElement.querySelectorAll('button.expand-toggle');
    (toggles[index] as HTMLButtonElement).click();
    fixture.detectChanges();
  }

  it('keeps the same panel instance across a poll with new objects', () => {
    poll([makeWorker({ id: 1 }), makeWorker({ id: 2, hostname: 'render-02' })]);
    expand(1);
    const before = panels();
    expect(before.length).toBe(1);
    expect(before[0].worker.id).toBe(2);

    poll([makeWorker({ id: 1 }), makeWorker({ id: 2, hostname: 'render-02', status: 'RENDERING' })]);
    const after = panels();
    expect(after.length).toBe(1);
    expect(after[0]).toBe(before[0]);
    expect(after[0].worker.status).toBe('RENDERING');
  });

  it('keeps the expansion when another worker leaves the list', () => {
    poll([makeWorker({ id: 1 }), makeWorker({ id: 2 })]);
    expand(1);
    const before = panels()[0];
    poll([makeWorker({ id: 2 })]);
    expect(panels().length).toBe(1);
    expect(panels()[0]).toBe(before);
    expect(fixture.componentInstance.expandedIds.has(2)).toBeTrue();
  });

  it('keeps an in-flight dismissal across a poll and applies its body at once', () => {
    const alert = makeAlert();
    const answer = new Subject<DismissCardAlertResponse>();
    service.dismissCardAlert.and.returnValue(answer.asObservable());
    const worker = () => makeWorker({ id: 1, card_status: makeStatus({ alerts: [alert] }) });
    poll([worker()]);
    expand(0);

    const alertsBefore = fixture.debugElement.query(By.directive(WorkerCardAlertsComponent))
      .componentInstance as WorkerCardAlertsComponent;
    (fixture.nativeElement.querySelector('button.dismiss') as HTMLButtonElement).click();
    fixture.detectChanges();
    poll([worker()]);

    const alertsAfter = fixture.debugElement.query(By.directive(WorkerCardAlertsComponent))
      .componentInstance as WorkerCardAlertsComponent;
    expect(alertsAfter).toBe(alertsBefore);
    expect((fixture.nativeElement.querySelector('button.dismiss') as HTMLButtonElement).disabled).toBeTrue();

    answer.next({ card_status: makeStatus({ alerts: [alert] }), card_alert_dismissals: [alert.id] });
    answer.complete();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.dismissal-sent')).not.toBeNull();
    expect(fixture.componentInstance.workers[0].card_alert_dismissals).toEqual([alert.id]);
  });

  describe('after a failed poll (#261)', () => {
    function failPoll(): void {
      const onError = service.pollList.calls.mostRecent().args[0];
      expect(onError).toEqual(jasmine.any(Function));
      onError!(new Error('manager down'));
      fixture.detectChanges();
    }

    function note(): string {
      return (fixture.nativeElement.querySelector('.update-failed') as HTMLElement).textContent!.trim();
    }

    it('keeps its rows, panel and expansion and says the update failed', () => {
      poll([makeWorker({ id: 1 }), makeWorker({ id: 2, hostname: 'render-02' })]);
      expand(1);
      const before = panels()[0];
      const rowsBefore = fixture.nativeElement.querySelectorAll('tr.worker-row').length;

      failPoll();
      expect(fixture.nativeElement.querySelectorAll('tr.worker-row').length).toBe(rowsBefore);
      expect(panels().length).toBe(1);
      expect(panels()[0]).toBe(before);
      expect(fixture.componentInstance.expandedIds.has(2)).toBeTrue();
      expect(note()).toBe('Last update failed. Retrying.');

      poll([makeWorker({ id: 1 }), makeWorker({ id: 2, hostname: 'render-02', status: 'RENDERING' })]);
      expect(panels()[0]).toBe(before);
      expect(panels()[0].worker.status).toBe('RENDERING');
      expect(note()).toBe('');
    });

    it('drops the "Retrying" note when polling ends with an auth error', () => {
      poll([makeWorker({ id: 1 })]);
      failPoll();
      list$.error(new Error('403'));
      fixture.detectChanges();
      expect(note()).toBe('');
      expect(fixture.nativeElement.querySelectorAll('tr.worker-row').length).toBe(1);
    });

    it('stops the spinner when the first poll fails', () => {
      expect(fixture.nativeElement.querySelector('mat-spinner')).not.toBeNull();
      failPoll();
      expect(fixture.nativeElement.querySelector('mat-spinner')).toBeNull();
      expect(note()).toBe('Last update failed. Retrying.');
    });
  });

  describe('the card choice form (R1)', () => {
    const twoCards = (revision: number): Worker => makeWorker({ id: 1, card_status: makeStatus({
      revision, cards: [makeCard(), makeCard({ key: 'pci:0000:02:00.0', ticked: false, state: 'unticked' })],
    }) });

    function form(): WorkerCardChoiceFormComponent {
      return fixture.debugElement.query(By.directive(WorkerCardChoiceFormComponent)).componentInstance;
    }

    function openForm(): void {
      poll([twoCards(3)]);
      expand(0);
      (fixture.nativeElement.querySelector('button.change') as HTMLButtonElement).click();
      fixture.detectChanges();
    }

    it('keeps the same form instance and the edits across a poll with new objects', () => {
      openForm();
      const before = form();
      const boxes = fixture.nativeElement.querySelectorAll('.card-tick input[type="checkbox"]');
      (boxes[1] as HTMLInputElement).click();
      fixture.detectChanges();

      poll([twoCards(3)]);
      expect(form()).toBe(before);
      expect([...form().ticked]).toEqual(['pci:0000:01:00.0', 'pci:0000:02:00.0']);
      expect((fixture.nativeElement.querySelector('button.send') as HTMLButtonElement).disabled).toBeFalse();

      poll([twoCards(4)]);
      expect(form()).toBe(before);
      expect(fixture.nativeElement.querySelector('.stale').textContent).toContain(STALE_TEXT);
      expect((fixture.nativeElement.querySelector('button.send') as HTMLButtonElement).disabled).toBeTrue();
    });

    it('applies the 202 body to the row at once: "Change pending"', () => {
      service.sendCardChoice.and.returnValue(of({ card_choice_request: makeRequest(), replaced: null }));
      openForm();
      (fixture.nativeElement.querySelector('button.send') as HTMLButtonElement).click();
      fixture.detectChanges();
      expect(fixture.nativeElement.querySelector('.change-pending-chip')).not.toBeNull();
      expect(fixture.nativeElement.querySelector('.pending')).not.toBeNull();
      expect(fixture.componentInstance.workers[0].card_choice_request).toEqual(makeRequest());
    });
  });

  it('drops the alert chip once an applied body has no alerts', () => {
    poll([makeWorker({ id: 1, card_status: makeStatus({ alerts: [makeAlert()] }) })]);
    expect(fixture.nativeElement.querySelector('.card-alert-chip')).not.toBeNull();
    fixture.componentInstance.applyCardUpdate(1, { card_status: makeStatus(), card_alert_dismissals: [] });
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.card-alert-chip')).toBeNull();
  });
});
