// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { ActivatedRoute, convertToParamMap } from '@angular/router';
import {
  BootstrapErrorComponent,
  _bootstrapErrorNav,
} from './bootstrap-error.component';

const STORAGE_KEY = 'sethlans.bootstrapError';

describe('BootstrapErrorComponent', () => {
  let fixture: ComponentFixture<BootstrapErrorComponent>;
  let component: BootstrapErrorComponent;

  function configure(
    queryParams: Record<string, string> = {},
  ): Promise<void> {
    return TestBed.configureTestingModule({
      imports: [BootstrapErrorComponent, NoopAnimationsModule],
      providers: [
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: { queryParamMap: convertToParamMap(queryParams) },
          },
        },
      ],
    }).compileComponents();
  }

  beforeEach(() => sessionStorage.clear());
  afterEach(() => sessionStorage.clear());

  it('reads stored error and renders invalid_token copy', async () => {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ code: 'invalid_token', message: 'Token expired' }),
    );
    await configure();
    fixture = TestBed.createComponent(BootstrapErrorComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();

    expect(component.code()).toBe('invalid_token');
    expect(component.message()).toBe('Token expired');
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('invalid_token');
    expect(text).toContain('Token expired');
  });

  it('clears sessionStorage entry on init', async () => {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ code: 'rate_limited' }),
    );
    await configure();
    fixture = TestBed.createComponent(BootstrapErrorComponent);
    fixture.detectChanges();
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('falls back to rate_limited copy when no stored message', async () => {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ code: 'rate_limited' }),
    );
    await configure();
    fixture = TestBed.createComponent(BootstrapErrorComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
    expect(component.code()).toBe('rate_limited');
    expect(component.message()).toContain('Too many attempts');
  });

  it('uses generic fallback copy for unknown codes', async () => {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ code: 'network_error' }),
    );
    await configure();
    fixture = TestBed.createComponent(BootstrapErrorComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
    expect(component.code()).toBe('network_error');
    expect(component.message()).toContain('Bootstrap failed');
  });

  it('falls back to query param ?code= when no sessionStorage', async () => {
    await configure({ code: 'invalid_token' });
    fixture = TestBed.createComponent(BootstrapErrorComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
    expect(component.code()).toBe('invalid_token');
    expect(component.message()).toContain('Token expired or incorrect');
  });

  it('defaults to internal_error when nothing provided', async () => {
    await configure();
    fixture = TestBed.createComponent(BootstrapErrorComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
    expect(component.code()).toBe('internal_error');
  });

  it('retry() triggers reload via window.location.href', async () => {
    await configure();
    fixture = TestBed.createComponent(BootstrapErrorComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();

    const goToSpy = jasmine.createSpy('goTo');
    const original = _bootstrapErrorNav.goTo;
    _bootstrapErrorNav.goTo = goToSpy;
    try {
      component.retry();
      expect(goToSpy).toHaveBeenCalledWith('/setup/');
    } finally {
      _bootstrapErrorNav.goTo = original;
    }
  });

  it('renders a Retry button that invokes retry()', async () => {
    await configure();
    fixture = TestBed.createComponent(BootstrapErrorComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();

    const spy = spyOn(component, 'retry');
    const button: HTMLButtonElement | null =
      (fixture.nativeElement as HTMLElement).querySelector(
        'button[mat-raised-button]',
      );
    expect(button).not.toBeNull();
    button!.click();
    expect(spy).toHaveBeenCalled();
  });

  it('does not render inside a mat-dialog-container', async () => {
    await configure();
    fixture = TestBed.createComponent(BootstrapErrorComponent);
    fixture.detectChanges();
    const host: HTMLElement = fixture.nativeElement;
    expect(host.closest('mat-dialog-container')).toBeNull();
  });
});
