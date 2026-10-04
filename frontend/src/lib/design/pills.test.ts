import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

/*
 * Every pill in Sift centres its contents the same way.
 *
 * ## What goes wrong
 *
 * A chip and a badge sit next to each other on a job row, on a download and on a file's details.
 * They look like the same object at a glance, and a badge that does not pin its line height to 1
 * carries the descender depth of a font nobody is using descenders from, and rides about a pixel
 * low inside its pill. One pixel is not noticeable. Two pills a pixel apart on one line is
 * noticeable, and it reads as cheapness rather than as anything a person can point at.
 *
 * ## Why this is a source test rather than a rendered one
 *
 * Because the failure is a MISSING declaration, and a missing declaration renders perfectly well. A
 * rendered test would have to measure to a sub-pixel and would go green the moment a typeface
 * changed its metrics. What is actually being held is that these four rules agree, and that is a
 * fact about the stylesheets.
 *
 * A rendered check still has its place and it lives in the browser suite, where the two are drawn
 * side by side. This one is the cheap ratchet that keeps them from drifting between those runs.
 */

const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const COMMON = join(SOURCE, 'lib', 'components', 'common');

/**
 * The pill-shaped things. Every one of them is a small rounded box with something centred in it,
 * and they appear beside one another.
 */
const PILLS = ['Chip.svelte', 'Badge.svelte'];

/**
 * What a pill has to declare on the box itself.
 *
 * `line-height: 1` is the one this file is for. The rest are here so that a new pill written
 * tomorrow gets the whole set rather than the half somebody remembered.
 *
 * Written WITH the semicolon, and that is not tidiness. `line-height: 1.2` contains the characters
 * `line-height: 1`, so a check for the text alone passes against the exact value it exists to
 * refuse.
 */
const REQUIRED = ['display: inline-flex;', 'align-items: center;', 'line-height: 1;'];

function sourceOf(file: string): string {
	return readFileSync(join(COMMON, file), 'utf8');
}

/** The one rule that dresses the pill itself, found by its class. */
function pillRule(source: string, className: string): string {
	const start = source.indexOf(`\n\t.${className} {`);
	expect(start, `no .${className} rule to read`).toBeGreaterThan(-1);
	const end = source.indexOf('\n\t}', start);
	return source.slice(start, end);
}

describe('every pill centres its contents the same way', () => {
	for (const file of PILLS) {
		const className = file.replace('.svelte', '').toLowerCase();

		it(`${file} declares the whole centring set`, () => {
			const rule = pillRule(sourceOf(file), className);
			for (const declaration of REQUIRED) {
				expect(rule, `${file} is missing "${declaration}"`).toContain(declaration);
			}
		});

		it(`${file} sizes itself with a logical property`, () => {
			/* `height` and `block-size` are the same thing until they are not, and having two spellings
			   of one measurement across components that sit side by side is how the two come to hold
			   different values without anybody comparing them. */
			const rule = pillRule(sourceOf(file), className);
			expect(rule, `${file} uses the physical "height"`).not.toMatch(/\n\t\theight:/);
		});
	}

	it('is reading real files, not an empty list', () => {
		/* A sweep that measured nothing would pass forever. If these are ever renamed, this fails
		   rather than quietly checking nothing at all. */
		expect(PILLS.length).toBeGreaterThan(1);
		for (const file of PILLS) expect(sourceOf(file).length).toBeGreaterThan(500);
	});
});
