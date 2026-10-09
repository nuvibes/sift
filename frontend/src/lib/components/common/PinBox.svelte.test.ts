/* The PIN control: the cells say how many digits, and no digit is drawn. What this file wrote
 * is tested: the transformer that drops what is not a digit from a paste. */

import { afterEach, beforeEach, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import PinBox, { PIN_DIGITS } from './PinBox.svelte';

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

function draw(props: Record<string, unknown> = {}): void {
	drawn = mount(PinBox, { target: host, props }) as Record<string, unknown>;
	flushSync();
}

function cells(): HTMLElement[] {
	return [...host.querySelectorAll<HTMLElement>('.ui-pin-cell')];
}

it('draws one cell per digit a PIN has', () => {
	/* Six cells; an older, shorter PIN leaves the last empty. */
	draw();

	expect(cells()).toHaveLength(PIN_DIGITS);
});

it('masks the digits and says how many have been typed', () => {
	/* No digit reaches the page as text, while the cells say how far the typing is. */
	draw({ value: '4172' });

	expect(host.querySelector('.pin-box')?.textContent?.trim()).toBe('');
	expect(cells().map((one) => one.querySelectorAll('.dot').length)).toEqual([1, 1, 1, 1, 0, 0]);
});

it('marks a refused PIN on the CELLS, which is the element the rule is on', () => {
	/* The class sits where the cells are: the primitive puts aria-invalid on its hidden input. */
	draw({ value: '9999', invalid: true });

	expect(host.querySelector('input')?.getAttribute('aria-invalid')).toBe('true');
	expect(host.querySelector('.pin-box')?.classList.contains('refused')).toBe(true);
	// The sentence saying WHICH PIN was wrong belongs to the screen, not to the control.
	expect(host.textContent).not.toContain('not right');
});

it('takes the hyphens and spaces out of a pasted PIN', () => {
	/* A spaced paste is cleaned; the clipboard is stood in for, as jsdom has no DataTransfer. */
	draw();
	const input = host.querySelector('input') as HTMLInputElement;
	const paste = new Event('paste', { bubbles: true, cancelable: true });
	Object.defineProperty(paste, 'clipboardData', {
		value: { getData: () => '41 72-96' }
	});
	input.dispatchEvent(paste);
	flushSync();

	expect(input.value).toBe('417296');
});
