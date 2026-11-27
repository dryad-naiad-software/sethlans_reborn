// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { setupCard, setupForm } from '../setup.fixtures';
import { ComputeMethod, FieldErrors, SetupCard, SetupForm } from '../setup.types';
import { SetupCardsComponent } from './setup-cards.component';

const NVIDIA = setupCard();
const NVIDIA_2 = setupCard({ address: 'gpu1', pci: '0000:02:00.0' });
const AMD_SCREEN = setupCard({ address: 'gpu2', name: 'Radeon RX 7900 XTX', brand: 'AMD', screen: true });
const MAIN_SCREEN = setupCard({ address: 'gpu3', name: 'GeForce RTX 4070', screen: true, primary_screen: true });

describe('SetupCardsComponent', () => {
  let fixture: ComponentFixture<SetupCardsComponent>;
  let form: SetupForm;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SetupCardsComponent, NoopAnimationsModule],
    }).compileComponents();
    fixture = TestBed.createComponent(SetupCardsComponent);
    form = setupForm();
  });

  function render(cards: SetupCard[], errors: FieldErrors = {}): void {
    form.cards = cards;
    fixture.componentRef.setInput('form', form);
    fixture.componentRef.setInput('errors', errors);
    fixture.detectChanges();
  }

  function checkboxes(): HTMLInputElement[] {
    return Array.from(fixture.nativeElement.querySelectorAll('mat-checkbox input'));
  }

  function warnings(): string[] {
    return Array.from(fixture.nativeElement.querySelectorAll('.setup-warning'))
      .map(node => (node as HTMLElement).textContent!.trim().replace(/\s+/g, ' '));
  }

  function hasCombine(): boolean {
    return fixture.nativeElement.textContent.includes('Combine GPUs');
  }

  /** worker-card-selection O1: one checkbox per card, none ticked at first, no Combine. */
  it('ticks no card on a fresh form', () => {
    render([NVIDIA, NVIDIA_2]);
    expect(checkboxes().length).toBe(2);
    expect(checkboxes().some(box => box.checked)).toBeFalse();
    expect(hasCombine()).toBeFalse();
  });

  /** O1: Combine GPUs appears with two or more ticked cards and is cleared below two. */
  it('offers Combine only with two or more ticked cards', () => {
    render([NVIDIA, NVIDIA_2]);
    checkboxes()[0].click();
    fixture.detectChanges();
    expect(form.worker.cards).toEqual(['gpu0']);
    expect(hasCombine()).toBeFalse();

    checkboxes()[1].click();
    fixture.detectChanges();
    expect(form.worker.cards).toEqual(['gpu0', 'gpu1']);
    expect(hasCombine()).toBeTrue();

    form.worker.combine = true;
    checkboxes()[0].click();
    fixture.detectChanges();
    expect(form.worker.cards).toEqual(['gpu1']);
    expect(form.worker.combine).toBeFalse();
    expect(hasCombine()).toBeFalse();
  });

  /**
   * O1, O10: GPU with no card is refused, a ticked screen card is warned
   * about (main screen first), and brands are not mixed. A server message
   * on worker.cards replaces the page's own warning.
   */
  const cases: { why: string; compute: ComputeMethod; cards: SetupCard[]; ticked: string[];
    errors?: FieldErrors; expected: string[] }[] = [
    { why: 'CPU only, nothing ticked', compute: 'cpu', cards: [NVIDIA], ticked: [], expected: [] },
    { why: 'GPU with no card', compute: 'gpu', cards: [NVIDIA], ticked: [],
      expected: ['GPU rendering needs at least one ticked graphics card.'] },
    { why: 'GPU with no card, the server said why', compute: 'cpu_gpu', cards: [NVIDIA], ticked: [],
      errors: { 'worker.cards': ['Tick at least one card.'] }, expected: [] },
    { why: 'a ticked card drives a screen', compute: 'gpu', cards: [NVIDIA, MAIN_SCREEN], ticked: ['gpu3'],
      expected: ['GeForce RTX 4070 drives the main screen; rendering on it can make the desktop slow.'] },
    { why: 'mixed brands', compute: 'gpu', cards: [NVIDIA, AMD_SCREEN], ticked: ['gpu0', 'gpu2'],
      expected: [
        'Radeon RX 7900 XTX drives a screen; rendering on it can make the desktop slow.',
        'Cards of one brand only can be ticked together.',
      ] },
    // Review B1: a saved card not on this machine does not count as a ticked card.
    { why: 'only a saved card that is not here', compute: 'gpu', cards: [NVIDIA], ticked: ['gpu9'],
      expected: [
        'A card chosen earlier was not found on this machine; untick it to continue.',
        'GPU rendering needs at least one ticked graphics card.',
      ] },
  ];
  for (const c of cases) {
    it(`warns per O1: ${c.why}`, () => {
      form.worker.compute = c.compute;
      form.worker.cards = c.ticked;
      render(c.cards, c.errors);
      expect(warnings()).toEqual(c.expected);
    });
  }

  /** O1, FR-WZ-4: SETHLANS_COMPUTE_* env vars set the choice, so the page says so and locks it. */
  it('notes and disables the controls under an environment override', async () => {
    form.worker.compute_env_override = true;
    form.worker.cards = ['gpu0', 'gpu1'];
    render([NVIDIA, NVIDIA_2]);
    // ngModel applies [disabled] after a resolved promise.
    await fixture.whenStable();
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('SETHLANS_COMPUTE_* environment variables');
    const radios: HTMLInputElement[] = Array.from(fixture.nativeElement.querySelectorAll('mat-radio-button input'));
    expect(radios.length).toBe(3);
    expect(radios.every(radio => radio.disabled)).toBeTrue();
    expect(checkboxes().length).toBe(3);
    expect(checkboxes().every(box => box.disabled)).toBeTrue();
  });

  /**
   * Review B1, owner rule "never trust a saved device list": a saved card
   * this machine lacks keeps its own ticked box (usable even under an env
   * override) until unticked, and does not count towards Combine.
   */
  it('shows a saved card that is not found until it is unticked', async () => {
    form.worker.compute_env_override = true;
    form.worker.cards = ['gpu9', 'gpu0'];
    render([NVIDIA]);
    await fixture.whenStable();
    fixture.detectChanges();

    const missing = fixture.nativeElement.querySelector('mat-checkbox.missing-card') as HTMLElement;
    expect(missing.textContent!.replace(/\s+/g, ' ')).toContain('gpu9 — not found on this machine');
    const box = missing.querySelector('input') as HTMLInputElement;
    expect(box.checked).toBeTrue();
    expect(box.disabled).toBeFalse();
    expect(hasCombine()).toBeFalse();

    box.click();
    fixture.detectChanges();
    expect(form.worker.cards).toEqual(['gpu0']);
    expect(fixture.nativeElement.querySelector('mat-checkbox.missing-card')).toBeNull();
    expect(warnings()).toEqual([]);
  });
});
