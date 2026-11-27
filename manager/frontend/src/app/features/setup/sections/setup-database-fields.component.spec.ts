// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { setupForm } from '../setup.fixtures';
import { SetupForm } from '../setup.types';
import { SetupDatabaseFieldsComponent, isLoopbackHost } from './setup-database-fields.component';

const UNENCRYPTED = 'will not be encrypted';

describe('SetupDatabaseFieldsComponent', () => {
  let fixture: ComponentFixture<SetupDatabaseFieldsComponent>;
  let form: SetupForm;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SetupDatabaseFieldsComponent, NoopAnimationsModule],
    }).compileComponents();
    fixture = TestBed.createComponent(SetupDatabaseFieldsComponent);
    form = setupForm();
  });

  async function render(): Promise<void> {
    fixture.componentRef.setInput('form', form);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  }

  async function typeHost(host: string): Promise<void> {
    const input = fixture.nativeElement.querySelector('input.mat-mdc-input-element') as HTMLInputElement;
    input.value = host;
    input.dispatchEvent(new Event('input'));
    await render();
  }

  function text(): string {
    return fixture.nativeElement.textContent;
  }

  /** FR-WZ-7b: loopback as the server's net.is_loopback_host (localhost, 127.0.0.0/8, ::1). */
  const hosts: [string, boolean][] = [
    [' LocalHost ', true], ['127.0.0.1', true], ['127.255.0.9', true], ['[::1]', true],
    ['', false], ['192.168.1.5', false], ['db.example.com', false], ['127.0.0', false],
  ];
  for (const [host, loopback] of hosts) {
    it(`treats "${host}" as ${loopback ? '' : 'not '}loopback`, () => {
      expect(isLoopbackHost(host)).toBe(loopback);
    });
  }

  /** FR-WZ-7b: on a new database, "Use TLS" follows the host until the person sets it. */
  it('turns TLS on for a host that is not loopback until the person sets it', async () => {
    await render();
    form.database.engine = 'postgresql';
    await render();

    await typeHost('db.example.com');
    expect(form.database.tls).toBeTrue();
    expect(text()).toContain('TLS mode');
    await typeHost('localhost');
    expect(form.database.tls).toBeFalse();

    (fixture.nativeElement.querySelector('mat-checkbox input') as HTMLInputElement).click();
    await render();
    expect(form.database.tls).toBeTrue();
    await typeHost('127.0.0.1');
    expect(form.database.tls).toBeTrue();
  });

  /** FR-WZ-7b, FR-RS-3: a stored PostgreSQL or MySQL setting keeps its stored TLS value. */
  it('keeps the stored TLS value of a stored database', async () => {
    form.database.engine = 'mysql';
    form.database.host = 'db.local';
    await render();
    await typeHost('db.example.com');
    expect(form.database.tls).toBeFalse();
  });

  /** FR-WZ-7b: TLS off to a host that is not loopback shows a warning; submit still allows it. */
  const warnings: { host: string; tls: boolean; warned: boolean }[] = [
    { host: 'db.example.com', tls: false, warned: true },
    { host: '127.0.0.1', tls: false, warned: false },
    { host: 'db.example.com', tls: true, warned: false },
  ];
  for (const c of warnings) {
    it(`warns about no TLS: host ${c.host}, TLS ${c.tls ? 'on' : 'off'}`, async () => {
      form.database = { ...form.database, engine: 'postgresql', host: c.host, tls: c.tls };
      await render();
      expect(text().includes(UNENCRYPTED)).toBe(c.warned);
    });
  }

  /** FR-WZ-1: the stored password is never sent back; an empty field keeps it. */
  it('says an empty password keeps the stored one', async () => {
    form.database = { ...form.database, engine: 'postgresql', host: 'db.local', password_set: true };
    await render();
    const password = fixture.nativeElement.querySelector('input[type=password]') as HTMLInputElement;
    expect(password.value).toBe('');
    expect(password.placeholder).toBe('Empty: keep the stored password');
  });
});
