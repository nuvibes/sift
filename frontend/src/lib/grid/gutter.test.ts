/*
 * The stylesheet and the grid's maths agree about how far apart tiles are.
 *
 * The gap has to exist twice: CSS cannot read TypeScript, and the justified layout is pure
 * arithmetic that must not reach into the DOM to find out. Two declarations of one number is the
 * shape that fails silently: somebody changes it in the place they happen to be looking at, and the
 * wall's spacing and its measured widths stop matching.
 *
 * That is not a cosmetic mismatch. The layout scales each row to span the container EXACTLY, and it
 * subtracts the gutter per gap to work out how much width the pictures may take. If the stylesheet
 * draws a wider gap than the maths subtracted, every row overflows by the difference times the
 * number of gaps, which on a row of eight tiles is seven times the error, and shows up as the last
 * tile clipped or the wall growing a horizontal scrollbar.
 */

import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { GRID_GUTTER } from './justify';

const APP_CSS = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'app.css');

describe('the gutter is one number', () => {
	it('the stylesheet declares what the layout subtracts', () => {
		const declared = readFileSync(APP_CSS, 'utf8').match(/--grid-gutter:\s*(\d+)px/);

		expect(declared, '`--grid-gutter` is not declared in app.css, or not in pixels').not.toBeNull();
		expect(
			Number(declared?.[1]),
			'the stylesheet and `GRID_GUTTER` in lib/grid/justify.ts disagree. Every row is scaled to\n' +
				'span the container exactly, with the gutter subtracted per gap, so a wider gap in the\n' +
				'stylesheet than the maths allowed for overflows every row by the difference times the\n' +
				'number of gaps in it.'
		).toBe(GRID_GUTTER);
	});
});
