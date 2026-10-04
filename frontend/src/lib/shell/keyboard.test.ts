/*
 * The keyboard's reading: a field has the focus and the viewport lost a keyboard's height.
 */
import { afterEach, describe, expect, it } from 'vitest';

import { keyboard, keyboardShows, takesTyping } from './keyboard.svelte';

describe('what takes typing', () => {
	it('is a text field, a text area or editable text, and not a button or a box that reads only', () => {
		const text = document.createElement('input');
		const tick = Object.assign(document.createElement('input'), { type: 'checkbox' });
		const locked = Object.assign(document.createElement('input'), { readOnly: true });
		const area = document.createElement('textarea');
		expect(takesTyping(text)).toBe(true);
		expect(takesTyping(area)).toBe(true);
		expect(takesTyping(tick)).toBe(false);
		expect(takesTyping(locked)).toBe(false);
		expect(takesTyping(document.createElement('button'))).toBe(false);
		expect(takesTyping(null)).toBe(false);
	});
});

describe('how much shorter is a keyboard', () => {
	it('counts a keyboard, not a browser bar folding away', () => {
		expect(keyboardShows(362, 659)).toBe(true);
		expect(keyboardShows(609, 659)).toBe(false);
	});
});

describe('the watch', () => {
	let stop: (() => void) | undefined;
	afterEach(() => {
		stop?.();
		document.body.innerHTML = '';
	});

	it('is up while a focused field has a keyboard under it, and down when the focus leaves', async () => {
		const tall = window.innerHeight;
		stop = keyboard.watch();
		const field = document.createElement('input');
		document.body.append(field);
		field.focus();
		Object.defineProperty(window, 'innerHeight', { configurable: true, value: tall - 300 });
		window.dispatchEvent(new Event('resize'));
		expect(keyboard.up).toBe(true);

		field.blur();
		await new Promise((done) => setTimeout(done, 0));
		expect(keyboard.up).toBe(false);
		Object.defineProperty(window, 'innerHeight', { configurable: true, value: tall });
	});
});
