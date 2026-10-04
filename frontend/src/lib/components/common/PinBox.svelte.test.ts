/*
 * The PIN control, and the two claims it makes that a password box could not: how many digits are
 * wanted is visible, because the cells say it, and the digits themselves are not, because a vault
 * is opened in front of whoever else is in the room.
 *
 * The paste is the primitive's own. What is tested is the one part this file wrote: the transformer
 * that drops everything that is not a digit, since a PIN from a password manager or a message comes
 * with spaces or hyphens.
 */

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
	/* Six, because a PIN is six digits. A shorter one kept from before that rule still opens Hidden
	 * and leaves the last cells empty; fewer cells would make a six-digit PIN untypable. */
	draw();

	expect(cells()).toHaveLength(PIN_DIGITS);
});

it('masks the digits and says how many have been typed', () => {
	/* Both halves, and they are the whole of the trade. The PIN is the one thing keeping the vault
	 * shut and it is typed where somebody else can see the screen, so no digit is ever drawn:
	 * the value must not reach the page as text at all, which is what the second assertion holds.
	 * What the cells still say is how far along the typing is, which is what a single dotted box
	 * takes away and is why there are cells at all. */
	draw({ value: '4172' });

	expect(host.querySelector('.pin-box')?.textContent?.trim()).toBe('');
	expect(cells().map((one) => one.querySelectorAll('.dot').length)).toEqual([1, 1, 1, 1, 0, 0]);
});

it('marks a refused PIN on the CELLS, which is the element the rule is on', () => {
	/* Both halves matter and the second is why this test exists. `aria-invalid` is announced, and
	 * the primitive puts it on the hidden input it renders INSIDE the root, so a border rule
	 * written against the root would match nothing and never be drawn, and no other check would
	 * notice. The class is on the element the cells are actually under. */
	draw({ value: '9999', invalid: true });

	expect(host.querySelector('input')?.getAttribute('aria-invalid')).toBe('true');
	expect(host.querySelector('.pin-box')?.classList.contains('refused')).toBe(true);
	// The sentence saying WHICH PIN was wrong belongs to the screen, not to the control.
	expect(host.textContent).not.toContain('not right');
});

it('takes the hyphens and spaces out of a pasted PIN', () => {
	/* A PIN read off a screen or out of a message arrives spaced. A control that refused the whole
	 * paste would read as the paste not working, which is worse than one that cleans it up.
	 *
	 * The clipboard is stood in for rather than built: jsdom has no `DataTransfer`, and what the
	 * primitive reads off the event is one method. */
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
