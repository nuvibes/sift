/* Which layer a keystroke belongs to.
 *
 * One line of code and it is worth a file, because the obvious alternative to it reads better and
 * is wrong. Asking whether focus is inside this panel fails: a sheet puts focus back on whatever
 * opened it, and it does so while the Escape is still being delivered, so by the time the window
 * hears the keystroke focus is already back on a button inside the panel and the test says "mine"
 * on the exact keystroke it exists to refuse.
 *
 * What is left is a fact about the EVENT rather than about the document, and these are the two
 * states it can be in.
 */

import { expect, it } from 'vitest';

import { keystrokeIsUnanswered } from './layers';

it('claims a keystroke nothing above has answered', () => {
	const escape = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true });

	expect(keystrokeIsUnanswered(escape)).toBe(true);
});

it('gives up a keystroke a layer above has already answered', () => {
	// A sheet that answers Escape calls preventDefault on it. It listens on the DOCUMENT and this
	// runs on the WINDOW, so the document is always reached first, which is what makes this
	// reliable rather than a race.
	const escape = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true });
	escape.preventDefault();

	expect(keystrokeIsUnanswered(escape)).toBe(false);
});

it('reads the event and never the document, so an open sheet does not change the answer', () => {
	// The tempting rule is "is focus inside me". Focus is on a button in the panel at the moment the
	// window hears the keystroke, whichever layer answered it, so it cannot tell the two apart.
	const panel = document.createElement('button');
	document.body.append(panel);
	panel.focus();
	try {
		const answered = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true });
		answered.preventDefault();

		expect(keystrokeIsUnanswered(answered)).toBe(false);
	} finally {
		panel.remove();
	}
});
