/* What a paste that lands on the downloads screen means.
 *
 * Three rules, and each of them is a judgement that is wrong invisibly: a screen that took every
 * paste would empty somebody's search into the download box, a screen that took a paste with no
 * address in it would collect the sentences they were copying to a friend, and a screen that
 * replaced what was in the box would be the only thing here that destroys work by accident.
 */
import { describe, expect, it } from 'vitest';

import { forTheScreen, holdsALink, intoBox } from './paste';

describe('whether a paste is a link at all', () => {
	it('takes an address', () => {
		expect(holdsALink('https://example.test/clip/1')).toBe(true);
	});

	it('takes one that arrived with prose around it', () => {
		expect(holdsALink('look at this\nhttps://example.test/clip/1\nthanks')).toBe(true);
	});

	it('leaves prose alone', () => {
		expect(holdsALink('remind me to ask about the camera')).toBe(false);
	});

	/* A bare host is what somebody copies out of an address bar in a hurry, and it is also what
	   half a sentence looks like. The box takes an address; the refusal is the honest answer and
	   the paste still reaches whatever it landed in. */
	it('leaves an address with no scheme alone', () => {
		expect(holdsALink('example.test/clip/1')).toBe(false);
	});
});

describe('whose paste it is', () => {
	it('is the screen when it landed on the page', () => {
		expect(forTheScreen(document.createElement('div'))).toBe(true);
	});

	it('is not the screen when it landed in a box', () => {
		const field = document.createElement('input');
		expect(forTheScreen(field)).toBe(false);
	});

	/* A paste into something rich lands on whichever child the cursor was in, so the ancestor is
	   what has to be asked. Without this the search field's own wrapper would hand its pastes over. */
	it('is not the screen when it landed inside one', () => {
		const field = document.createElement('textarea');
		const wrapper = document.createElement('label');
		wrapper.append(field);
		document.body.append(wrapper);
		expect(forTheScreen(field)).toBe(false);
		wrapper.remove();
	});
});

describe('what goes in the box', () => {
	it('fills an empty box', () => {
		expect(intoBox('', 'https://example.test/one')).toBe('https://example.test/one');
	});

	it('keeps what is already there and adds a line', () => {
		expect(intoBox('https://example.test/one', 'https://example.test/two')).toBe(
			'https://example.test/one\nhttps://example.test/two'
		);
	});
});
