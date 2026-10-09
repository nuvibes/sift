/* The picker's arithmetic: the round trip and the two degenerate cases. No colour is written
 * here; each is built from numbers, as app.css is the one place colours are declared. */

import { expect, it } from 'vitest';

import { hueColour, toHex, toHsv, turn } from './hsv';

/** A colour, built rather than typed. */
const colour = (red: number, green: number, blue: number): string =>
	'#' + [red, green, blue].map((one) => one.toString(16).padStart(2, '0')).join('');

it('is refused anything that is not a colour', () => {
	expect(toHsv('burnt umber')).toBeNull();
	// Half of one, which is what somebody in the middle of typing has in the box.
	expect(toHsv(colour(170, 51, 0).slice(0, 4))).toBeNull();
	expect(toHsv('')).toBeNull();
});

it('comes back to the colour it started from, all over the cube', () => {
	/* A grid, since a converter is wrong in a region; steps of 17 are exact in one hex pair. */
	for (let red = 0; red <= 255; red += 17) {
		for (let green = 0; green <= 255; green += 51) {
			for (let blue = 0; blue <= 255; blue += 51) {
				const started = colour(red, green, blue);
				const parsed = toHsv(started);
				expect(parsed, `${started} did not read as a colour`).not.toBeNull();
				expect(toHex(parsed!), `${started} did not survive the trip`).toBe(started);
			}
		}
	}
});

it('reads a grey as having no saturation, and says nothing about its hue', () => {
	// A grey answers hue 0; the picker keeps the one it had.
	const parsed = toHsv(colour(128, 128, 128));
	expect(parsed?.saturation).toBe(0);
	expect(parsed?.hue).toBe(0);
});

it('reads black as having no brightness', () => {
	const parsed = toHsv(colour(0, 0, 0));
	expect(parsed?.value).toBe(0);
	expect(parsed?.saturation).toBe(0);
});

it('puts each primary where the circle says it is', () => {
	// The hue anchors, which a channel-order bug would miss while still round-tripping.
	expect(toHsv(colour(255, 0, 0))?.hue).toBe(0);
	expect(toHsv(colour(0, 255, 0))?.hue).toBe(120);
	expect(toHsv(colour(0, 0, 255))?.hue).toBe(240);
});

it('folds a hue back into the circle rather than running off the end of it', () => {
	expect(turn(370)).toBe(10);
	expect(turn(-10)).toBe(350);
	expect(turn(360)).toBe(0);
});

it('draws a pure hue at the top of the cube, for the square to be washed over', () => {
	// Full saturation and value: the square's right edge is the hue itself.
	expect(hueColour(0)).toBe(colour(255, 0, 0));
	expect(hueColour(240)).toBe(colour(0, 0, 255));
});
