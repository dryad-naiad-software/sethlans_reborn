// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Clipboard } from '@angular/cdk/clipboard';
import { MatSnackBar } from '@angular/material/snack-bar';
import { Observable, of, throwError } from 'rxjs';
import { AuthService, UserInfo } from '../../core/services/auth.service';
import {
  EnrollmentKeyNotice, EnrollmentKeyNoticeService,
} from '../../core/services/enrollment-key-notice.service';
import { EnrollmentKeyNoticeComponent } from './enrollment-key-notice.component';

const KEY = 'ABCD-EFGH-JKMN-PQRS';
const ADMIN: UserInfo = { username: 'admin', is_staff: true };

describe('EnrollmentKeyNoticeComponent', () => {
  let fixture: ComponentFixture<EnrollmentKeyNoticeComponent>;
  let notices: jasmine.SpyObj<EnrollmentKeyNoticeService>;
  let clipboard: jasmine.SpyObj<Clipboard>;
  let snackBar: jasmine.SpyObj<MatSnackBar>;
  let auth: { user: UserInfo | null };

  beforeEach(async () => {
    notices = jasmine.createSpyObj('EnrollmentKeyNoticeService', ['get', 'dismiss']);
    clipboard = jasmine.createSpyObj('Clipboard', ['copy']);
    snackBar = jasmine.createSpyObj('MatSnackBar', ['open']);
    auth = { user: ADMIN };
    await TestBed.configureTestingModule({
      imports: [EnrollmentKeyNoticeComponent],
      providers: [
        { provide: AuthService, useValue: auth },
        { provide: EnrollmentKeyNoticeService, useValue: notices },
        { provide: Clipboard, useValue: clipboard },
        { provide: MatSnackBar, useValue: snackBar },
      ],
    }).compileComponents();
  });

  function render(answer: Observable<EnrollmentKeyNotice>): void {
    notices.get.and.returnValue(answer);
    fixture = TestBed.createComponent(EnrollmentKeyNoticeComponent);
    fixture.detectChanges();
  }

  function text(): string {
    return fixture.nativeElement.textContent;
  }

  function button(label: string): HTMLButtonElement {
    return (Array.from(fixture.nativeElement.querySelectorAll('button')) as HTMLButtonElement[])
      .find(b => b.textContent!.trim() === label)!;
  }

  /** FR-WZ-9: only admins ask (the endpoint is admin only). */
  const users: [string, UserInfo | null, number][] = [
    ['no user', null, 0], ['a non-admin', { username: 'u', is_staff: false }, 0], ['an admin', ADMIN, 1],
  ];
  for (const [why, user, calls] of users) {
    it(`asks for the notice ${calls} time(s) for ${why}`, () => {
      auth.user = user;
      render(of({ show: true, enrollment_key: KEY }));
      expect(notices.get).toHaveBeenCalledTimes(calls);
      expect(text().includes(KEY)).toBe(calls === 1);
    });
  }

  /** FR-WZ-9: the key shows while `show`; nothing when hidden or when the read fails. */
  const answers: [string, Observable<EnrollmentKeyNotice>, boolean][] = [
    ['show: true', of({ show: true, enrollment_key: KEY }), true],
    ['show: false', of({ show: false, enrollment_key: null }), false],
    ['a failed read', throwError(() => new Error('403')), false],
  ];
  for (const [why, answer, shown] of answers) {
    it(`${shown ? 'shows' : 'shows nothing'} for ${why}`, () => {
      render(answer);
      expect(fixture.nativeElement.querySelector('mat-card') !== null).toBe(shown);
      expect(text().includes(KEY)).toBe(shown);
    });
  }

  /** FR-WZ-9: Copy says "Copied", or how to copy by hand when the browser refuses. */
  const copies: [boolean, string, number][] = [[true, 'Copied', 0], [false, 'Copy', 1]];
  for (const [copied, label, messages] of copies) {
    it(`copies the key (clipboard ${copied ? 'works' : 'refuses'})`, () => {
      clipboard.copy.and.returnValue(copied);
      render(of({ show: true, enrollment_key: KEY }));
      button('Copy').click();
      fixture.detectChanges();

      expect(clipboard.copy).toHaveBeenCalledOnceWith(KEY);
      expect(button(label)).toBeTruthy();
      expect(snackBar.open).toHaveBeenCalledTimes(messages);
      if (messages) {
        expect(snackBar.open.calls.mostRecent().args[0]).toContain('copy it by hand');
      }
    });
  }

  /** FR-WZ-9: Dismiss hides the notice for good; a failed dismiss keeps it and says so. */
  const dismissals: [string, Observable<EnrollmentKeyNotice>, boolean][] = [
    ['works', of({ show: false, enrollment_key: null }), false],
    ['fails', throwError(() => new Error('500')), true],
  ];
  for (const [why, answer, stays] of dismissals) {
    it(`dismisses the notice (the POST ${why})`, () => {
      notices.dismiss.and.returnValue(answer);
      render(of({ show: true, enrollment_key: KEY }));
      button('Dismiss').click();
      fixture.detectChanges();

      expect(notices.dismiss).toHaveBeenCalledTimes(1);
      expect(text().includes(KEY)).toBe(stays);
      expect(snackBar.open).toHaveBeenCalledTimes(stays ? 1 : 0);
      if (stays) {
        expect(button('Dismiss').disabled).toBeFalse();
      }
    });
  }
});
