// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { DOCUMENT } from '@angular/core';
import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { Observable, of, throwError } from 'rxjs';
import { SetupApiService } from './setup-api.service';
import { REDIRECT_SECONDS, SetupFinishComponent } from './setup-finish.component';
import { finishInfo } from './setup.fixtures';
import { FinishInfo, SetupHealth } from './setup.types';

const ORIGIN = 'https://render-01:8080';

describe('SetupFinishComponent', () => {
  let fixture: ComponentFixture<SetupFinishComponent>;
  let api: jasmine.SpyObj<SetupApiService>;
  let assign: jasmine.Spy;

  beforeEach(async () => {
    api = jasmine.createSpyObj('SetupApiService', ['health']);
    assign = jasmine.createSpy('assign');
    // Only the component's own DOCUMENT is faked, so leaving the page is observable.
    await TestBed.configureTestingModule({
      imports: [SetupFinishComponent, NoopAnimationsModule],
      providers: [{ provide: SetupApiService, useValue: api }],
    }).overrideComponent(SetupFinishComponent, {
      add: { providers: [{ provide: DOCUMENT, useValue: { location: { origin: ORIGIN, assign } } }] },
    }).compileComponents();
    fixture = TestBed.createComponent(SetupFinishComponent);
  });

  function start(info: FinishInfo): void {
    fixture.componentRef.setInput('info', info);
    fixture.componentRef.setInput('bootId', 'old-boot');
    fixture.detectChanges();
  }

  function text(): string {
    return fixture.nativeElement.textContent.replace(/\s+/g, ' ');
  }

  /** FR-WZ-8, OQ-7: worker only shows the enrollment result and that the page is done. */
  it('shows the worker result and the close text without polling', () => {
    start(finishInfo({
      mode: 'worker', restarting: false, first_admin: null,
      worker: { enrolled: true, kept_enrollment: false, manager_url: 'https://studio.local:8080',
        manager_id: 'a1b2', manager_name: 'Studio manager', cert_fingerprint: 'AB:CD:EF',
        status: 'pending' },
    }));

    expect(text()).toContain('This worker is enrolled with Studio manager.');
    expect(text()).toContain('https://studio.local:8080');
    expect(text()).toContain('AB:CD:EF');
    expect(text()).toContain('This page will now close');
    expect(api.health).not.toHaveBeenCalled();
    expect(assign).not.toHaveBeenCalled();
  });

  /** FR-WZ-8: same address, restarting until the manager answers with a new boot_id, then /login/. */
  it('waits for the manager and opens /login/', fakeAsync(() => {
    const replies: Observable<SetupHealth>[] = [
      throwError(() => ({ status: 0 })),
      of({ mode: 'normal', boot_id: 'new-boot' }),
    ];
    api.health.and.callFake(() => replies.shift()!);
    start(finishInfo({ url: `${ORIGIN}/` }));

    tick(1000);
    fixture.detectChanges();
    expect(text()).toContain('Sethlans is restarting');
    expect(assign).not.toHaveBeenCalled();

    tick(1000);
    expect(assign).toHaveBeenCalledOnceWith('/login/');
  }));

  /** FR-WZ-8, #348: setup mode back after a failed first start hands its summary on exactly. */
  it('#348 reports setup mode coming back with its health summary, unchanged', fakeAsync(() => {
    const summary = "The manager's first start failed. Enter the setup token to see why.";
    api.health.and.returnValue(of({ mode: 'setup', boot_id: 'new-boot', last_failure: summary }));
    const again = jasmine.createSpy('setupAgain');
    fixture.componentInstance.setupAgain.subscribe(again);
    start(finishInfo());

    tick(1000);

    expect(again).toHaveBeenCalledOnceWith(summary);
    expect(assign).not.toHaveBeenCalled();
  }));

  /** FR-WZ-8: a new address cannot be polled from here; it is shown and opened after 30 s. */
  const moves: { why: string; info: Partial<FinishInfo> }[] = [
    { why: 'another origin', info: { url: 'https://192.168.1.20:8080/' } },
    { why: 'the port changed', info: { url: 'https://render-01:8443/', port_changed: true } },
  ];
  for (const c of moves) {
    it(`shows the new address and goes there after 30 s: ${c.why}`, fakeAsync(() => {
      start(finishInfo(c.info));
      expect(text()).toContain(`The manager will answer at ${c.info.url}`);
      expect(text()).toContain(`goes there in ${REDIRECT_SECONDS} s`);

      tick(REDIRECT_SECONDS * 1000 - 1);
      expect(assign).not.toHaveBeenCalled();
      tick(1);

      expect(assign).toHaveBeenCalledOnceWith(c.info.url!);
      expect(api.health).not.toHaveBeenCalled();
    }));
  }
});
