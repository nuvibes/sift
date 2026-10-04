/* The arithmetic the colour picker is drawn in.
 *
 * What is worth testing is the ROUND TRIP and the two degenerate cases, because those are where a
 * picker goes wrong in a way nobody can see: a colour that comes back a shade off drifts a little
 * further every time the marker is touched, and a grey that reports a hue drags the hue slider
 * somewhere nobody put it.
 *
 * NO COLOUR IS WRITTEN HERE. Every one is built from numbers: a colour written into a file other
 * than `app.css` is a second declaration of one, and a test of a converter has no business making
 * one.
 */

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
	/* A grid rather than a handful, because a converter is wrong in a REGION and right on the
	   examples somebody happened to pick. Every step of 17 is exact in one hex digit pair, so what
	   is being measured is the arithmetic and not the rounding. */
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
	// Which hue a grey "was" is not a fact about the grey. The picker keeps the one it had; this
	// answers 0 rather than inventing one, and the two together are what stop the slider jumping.
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
	// The three corners the whole hue scale is anchored on: red at nought, green a third of the way
	// round, blue two thirds. A converter that has the channels in the wrong order still round-trips
	// perfectly and has every hue in the wrong place, which the test above cannot see.
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
	// Full saturation, full brightness: one channel at nothing and one at everything, which is what
	// makes the square's right-hand edge the hue itself rather than a shade of it.
	expect(hueColour(0)).toBe(colour(255, 0, 0));
	expect(hueColour(240)).toBe(colour(0, 0, 255));
});
