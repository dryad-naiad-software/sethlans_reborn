// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { of, throwError } from 'rxjs';
import { SetupApiService } from './setup-api.service';
import { SetupTokenComponent } from './setup-token.component';

describe('SetupTokenComponent', () => {
  let fixture: ComponentFixture<SetupTokenComponent>;
  let component: SetupTokenComponent;
  let api: jasmine.SpyObj<SetupApiService>;

  beforeEach(async () => {
    api = jasmine.createSpyObj('SetupApiService', ['auth']);
    await TestBed.configureTestingModule({
      imports: [SetupTokenComponent, NoopAnimationsModule],
      providers: [{ provide: SetupApiService, useValue: api }],
    }).compileComponents();
    fixture = TestBed.createComponent(SetupTokenComponent);
    component = fixture.componentInstance;
  });

  /** FR-SEC-1: the trimmed token is sent; a good one moves on to the form. */
  it('sends the trimmed token and moves on when it is accepted', () => {
    api.auth.and.returnValue(of(undefined));
    const authenticated = jasmine.createSpy('authenticated');
    component.authenticated.subscribe(authenticated);
    component.token = '  ABCD-1234 \n';

    component.send();

    expect(api.auth).toHaveBeenCalledOnceWith('ABCD-1234');
    expect(authenticated).toHaveBeenCalledTimes(1);
    expect(component.token).toBe('');
  });

  /** FR-SEC-1: a refused token shows the server's message and stays on the step. */
  it('shows the server message for a refused token', () => {
    api.auth.and.returnValue(throwError(() => ({
      status: 403, code: 'invalid_token', message: 'The setup token is not correct.', fields: {}, codes: {},
    })));
    component.token = 'WRONG';

    component.send();
    fixture.detectChanges();

    expect(component.sending).toBeFalse();
    expect(fixture.nativeElement.querySelector('.setup-error').textContent)
      .toContain('The setup token is not correct.');
  });
});
