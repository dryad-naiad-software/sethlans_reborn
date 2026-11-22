// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { MachineRow } from '../core/services/unreal-measurement.model';
import { MachineRowsComponent } from './machine-rows.component';
import { MACHINE_RAM, machineRow } from './machine-row.fixtures';

/** Per-machine memory test FR-MT-U-4: the machine list as plain rows. */

/** Text a farm could report; it must reach the page as text, never as markup. */
const HOSTILE = '<img src=x onerror="window.__xss=1"><b>bold</b>';

function text(el: Element | null | undefined): string {
  return (el?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

describe('MachineRowsComponent', () => {
  let fixture: ComponentFixture<MachineRowsComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [MachineRowsComponent] }).compileComponents();
  });

  function render(machines: MachineRow[] | null | undefined): HTMLElement {
    fixture = TestBed.createComponent(MachineRowsComponent);
    fixture.componentRef.setInput('machines', machines);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  function rows(el: HTMLElement): HTMLElement[] {
    return Array.from(el.querySelectorAll('.machine-row'));
  }

  it('renders nothing while the list is unknown (null)', () => {
    const el = render(null);
    expect(el.querySelector('.machines')).toBeNull();
    expect(text(el)).toBe('');
  });

  it('renders nothing when the list is undefined', () => {
    expect(render(undefined).querySelector('.machines')).toBeNull();
  });

  it('says "No machines." for an empty list', () => {
    const el = render([]);
    expect(text(el.querySelector('.machines-title'))).toBe('Machines');
    expect(text(el.querySelector('.machines-none'))).toBe('No machines.');
    expect(rows(el).length).toBe(0);
  });

  it('renders one row per machine in the order given', () => {
    const el = render([
      machineRow({ hostname: 'render-02' }),
      machineRow({ hostname: 'render-01', state: 'not_tried', online: false }),
      machineRow({ hostname: 'render-03', state: 'testing', card_name: null, card_kind: '10de:2b85:a1' }),
    ]);
    expect(el.querySelector('.machines-none')).toBeNull();
    expect(rows(el).map(r => text(r.querySelector('.machine-text')))).toEqual([
      'render-02, Radeon RX 7900 XT: fits: 13,689 of 20,464 MB',
      'render-01, Radeon RX 7900 XT: not tried yet (off)',
      'render-03, 10de:2b85:a1: testing',
    ]);
  });

  it('shows each state text in its row', () => {
    const el = render([
      machineRow({ state: 'does_not_fit', peak_mb: null, lower_bound_mb: 19012 }),
      machineRow({ state: 'failed', reason: 'Unreal crashed' }),
      machineRow({ state: 'failed', reason: null }),
      machineRow({ state: 'retest_due' }),
    ]);
    expect(rows(el).map(r => text(r.querySelector('.machine-text')))).toEqual([
      'render-01, Radeon RX 7900 XT: does not fit: at least 19,012 MB on a 20,464 MB card',
      'render-01, Radeon RX 7900 XT: test failed: Unreal crashed',
      'render-01, Radeon RX 7900 XT: test failed: no reason given',
      'render-01, Radeon RX 7900 XT: re-test due (driver changed)',
    ]);
  });

  it('marks a covered row with "(covered)" and leaves others unmarked', () => {
    const el = render([machineRow({ covered: true }), machineRow({ covered: false })]);
    expect(text(rows(el)[0].querySelector('.machine-covered'))).toBe('(covered)');
    expect(rows(el)[1].querySelector('.machine-covered')).toBeNull();
    expect(text(rows(el)[1])).not.toContain('(covered)');
  });

  it('shows the RAM figures of a row that has them', () => {
    const el = render([machineRow({ ram: MACHINE_RAM })]);
    expect(text(rows(el)[0].querySelector('.machine-ram .ram-peak')))
      .toBe('RAM peak 8200 MB physical, 11500 MB commit');
  });

  it('shows no RAM text for a row whose ram is null', () => {
    const el = render([machineRow({ ram: MACHINE_RAM }), machineRow({ ram: null })]);
    expect(rows(el)[1].querySelector('.machine-ram')).toBeNull();
    expect(text(rows(el)[1])).not.toContain('RAM peak');
  });

  it('shows a covered, offline row with RAM in one line', () => {
    const el = render([machineRow({ covered: true, online: false, ram: MACHINE_RAM })]);
    expect(text(rows(el)[0])).toBe('render-01, Radeon RX 7900 XT: fits: 13,689 of 20,464 MB (off) (covered) '
      + '· RAM peak 8200 MB physical, 11500 MB commit');
  });

  it('updates the rows when the input changes', () => {
    render([machineRow({ state: 'testing' })]);
    fixture.componentRef.setInput('machines', [machineRow()]);
    fixture.detectChanges();
    expect(text(fixture.nativeElement.querySelector('.machine-text')))
      .toBe('render-01, Radeon RX 7900 XT: fits: 13,689 of 20,464 MB');
  });

  it('shows farm text as text only, never as markup', () => {
    const el = render([machineRow({ hostname: HOSTILE, card_name: HOSTILE, state: 'failed', reason: HOSTILE })]);
    expect(text(rows(el)[0].querySelector('.machine-text'))).toBe(`${HOSTILE}, ${HOSTILE}: test failed: ${HOSTILE}`);
    expect(el.querySelector('img, b')).toBeNull();
    expect((window as unknown as { __xss?: number }).__xss).toBeUndefined();
  });
});
