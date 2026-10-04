// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The derived accent, measured rather than described.
 *
 * `accent.ts` claims that every family it returns clears the design system's floors on every base.
 * That claim is the whole reason a person is allowed to choose any colour at all, so it is put to a
 * grid of colours covering the sRGB cube against every base and the numbers are taken.
 *
 * NOTHING HERE NAMES A COLOUR. The bases' grounds are read out of `app.css`, which is the one
 * file allowed to know what colour anything is, so this measures the real palette rather than a
 * copy of it, and the repository's colour gate has nothing to forgive. The chosen colours are built
 * from numbers, which is honest as well as convenient: a grid is a better witness than six colours
 * somebody liked.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { accentFamily, contrast, isHex, wornDifferently, type Ground } from './accent';

const APP_CSS = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'app.css');

/** The stylesheet with its comments taken out, so a hex inside prose cannot be read as a value. */
const css = readFileSync(APP_CSS, 'utf8').replace(/\/\*[\s\S]*?\*\//g, ' ');

/** One custom property, as the block for `selector` sets it. */
function declared(selector: string, property: string): string {
	const block = new RegExp(`${selector}\\s*\\{([^}]*)\\}`).exec(css);
	expect(block, `no block for ${selector}`).not.toBeNull();
	const found = new RegExp(`${property}\\s*:\\s*([^;]+);`).exec(block![1]);
	expect(found, `${selector} does not set ${property}`).not.toBeNull();
	return found![1].trim();
}

/** The three grounds a base paints, exactly as the page would resolve them. */
function groundOf(base: string): Ground {
	const selector =
		base === 'midnight' ? ':root,\\s*\\[data-base=.midnight.\\]' : `\\[data-base='${base}'\\]`;
	return {
		canvas: declared(selector, '--p-bg'),
		surface3: declared(selector, '--p-surface-3'),
		surface4: declared(selector, '--p-surface-4')
	};
}

const BASES = ['obsidian', 'midnight', 'graphite', 'chrome'] as const;

const rgb = (value: string): [number, number, number] => {
	const digits = value.replace('#', '');
	return [0, 2, 4].map((at) => parseInt(digits.slice(at, at + 2), 16) / 255) as [
		number,
		number,
		number
	];
};

const WHITE: [number, number, number] = [1, 1, 1];

/** A grid over the sRGB cube: five steps per channel, every combination. 125 colours, which reaches
 *  every hue, both ends of the lightness range and everything from grey to fully saturated. */
function grid(): string[] {
	const steps = [0, 63, 127, 191, 255];
	const colours: string[] = [];
	for (const r of steps) {
		for (const g of steps) {
			for (const b of steps) {
				colours.push('#' + [r, g, b].map((n) => n.toString(16).padStart(2, '0')).join(''));
			}
		}
	}
	return colours;
}

describe('a derived accent', () => {
	it('reads every base out of the stylesheet', () => {
		// A gate that read nothing would measure nothing and pass. Every ground has to be a colour.
		for (const base of BASES) {
			const ground = groundOf(base);
			for (const value of [ground.canvas, ground.surface3, ground.surface4]) {
				expect(isHex(value), `${base} ground ${value}`).toBe(true);
			}
		}
	});

	for (const base of BASES) {
		it(`holds every floor on the ${base} base, for any colour chosen`, () => {
			const ground = groundOf(base);
			const canvas = rgb(ground.canvas);
			const card = rgb(ground.surface3);
			const chip = rgb(ground.surface4);
			const offences: string[] = [];

			for (const chosen of grid()) {
				const family = accentFamily(chosen, ground);
				const say = (what: string, ratio: number, floor: number) => {
					if (ratio < floor) {
						offences.push(`${chosen}: ${what} is ${ratio.toFixed(2)}:1, under ${floor}:1`);
					}
				};
				say('white on the fill', contrast(WHITE, rgb(family.accent)), 4.5);
				say('the fill on the canvas', contrast(rgb(family.accent), canvas), 3);
				say('the text on a card', contrast(rgb(family.text), card), 4.5);
				say('the text on the canvas', contrast(rgb(family.text), canvas), 4.5);
				say('the text on its own tint', contrast(rgb(family.text), rgb(family.bg)), 4.5);
				say("the ring's line on a chip", contrast(rgb(family.ring), chip), 3);
			}

			expect(offences.slice(0, 8), `${offences.length} of ${grid().length} colours`).toEqual([]);
		});
	}

	it('gives the shipped blue back when the shipped blue is chosen', () => {
		/* THE KNOWN POSITIVE, and the strongest one available: the six named accents came out of the
		 * five rules written in `app.css`, so running the same rules on one of them has to land back
		 * where it started. Within a step of an 8-bit channel, because the two were solved by
		 * different searches: what is being checked is the rule, not the arithmetic of rounding. */
		const ground = groundOf('midnight');
		const shipped = {
			accent: declared(':root,\\s*\\[data-accent=.blue.\\]', '--p-accent'),
			text: declared(':root,\\s*\\[data-accent=.blue.\\]', '--p-accent-text'),
			bg: declared(':root,\\s*\\[data-accent=.blue.\\]', '--p-accent-bg')
		};
		const derived = accentFamily(shipped.accent, ground);

		const close = (a: string, b: string, within: number) => {
			const [x, y] = [rgb(a), rgb(b)];
			return x.every((channel, at) => Math.abs(channel - y[at]) * 255 <= within);
		};
		expect(close(derived.accent, shipped.accent, 1), `${derived.accent} vs ${shipped.accent}`).toBe(
			true
		);
		expect(close(derived.bg, shipped.bg, 2), `${derived.bg} vs ${shipped.bg}`).toBe(true);
		expect(close(derived.text, shipped.text, 6), `${derived.text} vs ${shipped.text}`).toBe(true);
	});

	it('darkens a colour white cannot sit on, and lifts one the canvas swallows', () => {
		// The two ends, named: the fill is the one role pulled from both sides.
		const ground = groundOf('midnight');
		const white = accentFamily('#ffffff', ground);
		const black = accentFamily('#000000', ground);
		expect(contrast(WHITE, rgb(white.accent))).toBeGreaterThanOrEqual(4.5);
		expect(contrast(rgb(black.accent), rgb(ground.canvas))).toBeGreaterThanOrEqual(3);
	});

	it('keeps a muted colour muted', () => {
		/* A pale teal must not come back as the loudest teal the screen can make. Compared against
		 * the same hue chosen at full strength, which is the only honest way to say "muted". */
		const ground = groundOf('midnight');
		const muted = accentFamily('#6f9a99', ground);
		const loud = accentFamily('#00a3a0', ground);
		const spread = (value: string) => {
			const [r, g, b] = rgb(value);
			return Math.max(r, g, b) - Math.min(r, g, b);
		};
		expect(spread(muted.accent)).toBeLessThan(spread(loud.accent));
	});

	it('refuses anything that is not a six-digit colour', () => {
		expect(isHex('#2563eb')).toBe(true);
		expect(isHex('#2563EB')).toBe(true);
		expect(isHex('2563eb')).toBe(false);
		expect(isHex('#2563e')).toBe(false);
		expect(isHex('#2563ebb')).toBe(false);
		expect(isHex('#2563eg')).toBe(false);
		expect(isHex('')).toBe(false);
	});
});

describe('how a kept colour is worn differently', () => {
	/* A kept colour's dot is the fill it will be worn as, and its label says when that is not the
	   colour kept. Said only where the eye can tell the two apart, and in the direction it moved. */
	it('says darker for a pale colour that has to carry white words', () => {
		const pale = '#faf096';
		const worn = accentFamily(pale, groundOf('midnight')).accent;
		expect(wornDifferently(pale, worn)).toBe('darker');
	});

	it('says lighter for a colour too dark to stand off the canvas', () => {
		const deep = '#0a0a3c';
		const worn = accentFamily(deep, groundOf('obsidian')).accent;
		expect(wornDifferently(deep, worn)).toBe('lighter');
	});

	it('says nothing for a colour worn as it was kept, or for one rounding step', () => {
		const shipped = '#2563eb';
		expect(wornDifferently(shipped, shipped)).toBeNull();
		expect(wornDifferently(shipped, '#2563ec')).toBeNull();
		expect(wornDifferently('not a colour', shipped)).toBeNull();
	});

	it('says softer when only the colour in it went', () => {
		// The same lightness with the chroma taken out: a grey of the blue's lightness.
		const blue = '#2563eb';
		const grey = '#707070';
		expect(wornDifferently(blue, grey)).toBe('softer');
	});
});
