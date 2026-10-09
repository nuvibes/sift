import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

/* Every pill in Sift centres its contents the same way. */

const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const COMMON = join(SOURCE, 'lib', 'components', 'common');

/** The pill-shaped things. Every one of them is a small rounded box with something centred in it,
 * and they appear beside one another. */
const PILLS = ['Chip.svelte', 'Badge.svelte'];

/** What a pill has to declare on the box itself. */
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
			/* `height` and `block-size` are the same thing until they are not, and having two
			   spellings of one measurement across components that sit side by side is how the
			   two come to hold different values without anybody comparing them. */
			const rule = pillRule(sourceOf(file), className);
			expect(rule, `${file} uses the physical "height"`).not.toMatch(/\n\t\theight:/);
		});
	}

	it('is reading real files, not an empty list', () => {
		/* A sweep that measured nothing would pass forever. */
		expect(PILLS.length).toBeGreaterThan(1);
		for (const file of PILLS) expect(sourceOf(file).length).toBeGreaterThan(500);
	});
});
