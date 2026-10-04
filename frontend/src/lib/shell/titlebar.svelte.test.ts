import { describe, expect, it } from 'vitest';

import { asHex } from './titlebar.svelte';

/*
 * The colour the caption buttons get painted in has to survive a round trip the page did not choose.
 *
 * `app.css` writes every colour as hex; `getComputedStyle` hands it back in the browser's own
 * serialisation, which is `rgb(...)`; and the shell refuses anything that is not hex, because what
 * it does with the string is give it to the operating system. So this is the one conversion in that
 * chain, and the cases that matter are the ones where it must REFUSE rather than guess. A colour
 * Sift asked for and did not get is worse than the one already on the window.
 */
describe('the colour handed to the window', () => {
	it('passes hex straight through, in both lengths', () => {
		expect(asHex('#1e2024')).toBe('#1e2024');
		expect(asHex('  #abc  ')).toBe('#abc');
	});

	it('turns the browser&apos;s own serialisation into hex', () => {
		expect(asHex('rgb(30, 32, 36)')).toBe('#1e2024');
		// The space-separated form, which is what a modern browser answers for a colour written in
		// any of the newer syntaxes.
		expect(asHex('rgb(30 32 36)')).toBe('#1e2024');
		expect(asHex('rgba(30, 32, 36, 1)')).toBe('#1e2024');
	});

	it('pads a single digit, or the string is the wrong length and means another colour', () => {
		expect(asHex('rgb(1, 2, 3)')).toBe('#010203');
	});

	it('refuses a partly transparent colour rather than dropping the transparency', () => {
		// The site cannot draw one. Painting the solid version would be answering a question
		// nobody asked, in a colour nobody chose.
		expect(asHex('rgba(30, 32, 36, 0.5)')).toBeNull();
		expect(asHex('rgb(30 32 36 / 50%)')).toBeNull();
	});

	it('refuses anything it cannot read, and never guesses', () => {
		expect(asHex('')).toBeNull();
		expect(asHex('red')).toBeNull();
		expect(asHex('var(--sift-surface-1)')).toBeNull();
		expect(asHex('color-mix(in oklch, red, blue)')).toBeNull();
	});

	it('clamps rather than emitting a hex pair that is not two characters', () => {
		// An out-of-range channel is not something a stylesheet produces, and a `toString(16)` of it
		// would be three characters, a string the shell would refuse for the wrong reason.
		expect(asHex('rgb(300, -20, 36)')).toBe('#ff0024');
	});
});
