// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { ERROR_TOOLTIP_MAX, JobErrorMessageComponent } from './job-error-message.component';

describe('JobErrorMessageComponent', () => {
  let fixture: ComponentFixture<JobErrorMessageComponent>;
  let component: JobErrorMessageComponent;

  function render(message: string | null | undefined): HTMLElement {
    fixture.componentRef.setInput('message', message);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  const textEl = (el: HTMLElement) => el.querySelector('.job-error-text') as HTMLElement;
  const toggleEl = (el: HTMLElement) => el.querySelector('button.job-error-toggle') as HTMLButtonElement;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [JobErrorMessageComponent, NoopAnimationsModule],
    }).compileComponents();
    fixture = TestBed.createComponent(JobErrorMessageComponent);
    component = fixture.componentInstance;
  });

  it('renders the message as plain text outside any button', () => {
    const el = render('Blender exited with code 1');
    const text = textEl(el);
    expect(text.tagName).toBe('SPAN');
    expect(text.textContent!.trim()).toBe('Blender exited with code 1');
    expect(text.closest('button')).toBeNull();
    expect(getComputedStyle(text).userSelect).not.toBe('none');
  });

  for (const empty of [null, undefined, '', '   \n\t ']) {
    it(`renders no element for ${JSON.stringify(empty)}`, () => {
      const el = render(empty);
      expect(el.querySelector('.job-error')).toBeNull();
      expect(el.querySelector('button')).toBeNull();
      expect(el.textContent!.trim()).toBe('');
    });
  }

  it('renders markup as literal text, never as HTML', () => {
    const el = render('<img src=x onerror="alert(1)"><b>bold</b>');
    const text = textEl(el);
    expect(text.querySelector('img')).toBeNull();
    expect(text.querySelector('b')).toBeNull();
    expect(text.textContent).toContain('<b>bold</b>');
  });

  it('gives the toggle a short label tied to the text by a unique id', () => {
    const el = render('failed');
    const toggle = toggleEl(el);
    const id = textEl(el).id;
    expect(id).toMatch(/^job-error-text-\d+$/);
    expect(toggle.getAttribute('aria-controls')).toBe(id);
    expect(toggle.getAttribute('aria-label')).toBe('Show full error');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(textEl(el).hasAttribute('aria-label')).toBeFalse();

    const other = TestBed.createComponent(JobErrorMessageComponent);
    expect(other.componentInstance.textId).not.toBe(component.textId);
  });

  it('keeps short messages whole in the tooltip', () => {
    render('short failure');
    expect(component.tooltip).toBe('short failure');
  });

  it('caps the tooltip for long messages but keeps the full text in the element', () => {
    const long = 'x'.repeat(ERROR_TOOLTIP_MAX + 300);
    const el = render(long);
    expect(component.tooltip.length).toBeLessThan(long.length);
    expect(textEl(el).textContent!.trim()).toBe(long);
  });

  it('truncates to one line by default and expands when the toggle is clicked', () => {
    const el = render('Traceback (most recent call last):\n  File "render.py"');
    const text = textEl(el);
    expect(getComputedStyle(text).whiteSpace).toBe('nowrap');
    expect(getComputedStyle(text).textOverflow).toBe('ellipsis');

    toggleEl(el).click();
    fixture.detectChanges();
    expect(text.classList).toContain('expanded');
    expect(getComputedStyle(text).whiteSpace).toBe('pre-wrap');
    expect(toggleEl(el).getAttribute('aria-expanded')).toBe('true');
    expect(toggleEl(el).getAttribute('aria-label')).toBe('Hide full error');
    expect(text.tabIndex).toBe(0);

    toggleEl(el).click();
    fixture.detectChanges();
    expect(text.classList).not.toContain('expanded');
  });

  it('is operable from the keyboard through a native, focusable button', () => {
    const el = render('failed');
    const toggle = toggleEl(el);
    // Synthetic key events do not trigger a button's default activation in
    // the browser, so this asserts the native semantics that make Enter and
    // Space activate it, plus focus, then the activation (click) itself.
    expect(toggle.tagName).toBe('BUTTON');
    expect(toggle.type).toBe('button');
    expect(toggle.disabled).toBeFalse();
    toggle.focus();
    expect(document.activeElement).toBe(toggle);
    toggle.click();
    fixture.detectChanges();
    expect(component.expanded).toBeTrue();
  });

  it('does not collapse when Space or Enter is pressed on the expanded text', () => {
    const el = render('failed');
    toggleEl(el).click();
    fixture.detectChanges();
    const text = textEl(el);
    for (const key of [' ', 'Enter']) {
      text.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
    }
    fixture.detectChanges();
    expect(component.expanded).toBeTrue();
  });

  it('collapses when the message is cleared', () => {
    render('failed');
    component.expanded = true;
    const el = render('');
    expect(component.expanded).toBeFalse();
    expect(el.querySelector('.job-error')).toBeNull();
  });
});
