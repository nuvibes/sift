import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

/*
 * The three marks on a file's action row (heart, stars, O counter) are one row.
 *
 * Read off the stylesheets rather than a rendered element, because jsdom computes no cascade. The
 * counter must declare the values `Heart.svelte` declares, read from that file, so moving the
 * heart's box without the counter's fails here.
 */

function source(path: string): string {
	return readFileSync(resolve(path), 'utf8');
}

/** The declarations of the first rule whose selector is exactly `selector`, as a name -> value map. */
function rule(css: string, selector: string): Record<string, string> {
	const at = css.indexOf(`${selector} {`);
	expect(at, `no rule for ${selector}`).toBeGreaterThanOrEqual(0);
	const body = css.slice(css.indexOf('{', at) + 1, css.indexOf('}', at));
	const out: Record<string, string> = {};
	for (const [, name, value] of body
		.replace(/\/\*[\s\S]*?\*\//g, '')
		.matchAll(/([a-z-]+)\s*:\s*([^;]+);/g)) {
		out[name] = value.replace(/\s+/g, ' ').trim();
	}
	return out;
}

const heart = source('src/lib/components/common/Heart.svelte');
const row = source('src/lib/components/AssetDetailControls.svelte');

describe('the O counter on the action row', () => {
	it('sits in the same box as the heart, so the gaps either side of the star are equal', () => {
		const theHeart = rule(heart, '.heart');
		const theDrop = rule(row, '.detail-controls :global(.btn.o-mark)');

		expect(theDrop.padding).toBe(theHeart.padding);
		expect(theDrop['border-radius']).toBe(theHeart['border-radius']);
		expect(theDrop.color, 'a greyer resting mark than the heart beside it').toBe(theHeart.color);
	});

	it('grows under the pointer by the heart step, and is not underlined', () => {
		const theHeart = rule(heart, '.heart:hover');
		const theDrop = rule(row, '.detail-controls :global(.btn.o-mark:hover:not(:disabled))');

		expect(theDrop.transform).toBe(theHeart.transform);
		expect(theDrop['text-decoration']).toBe('none');
	});

	it('is the button those rules are written for', () => {
		/* A rule for a class nothing wears is a rule that styles nothing, and passes the two
		   assertions above. */
		const markup = row.slice(0, row.indexOf('<style>'));
		expect(markup).toMatch(/<Button[^>]*class="o-mark"/);
	});

	it('opens One fewer and Start again as a sheet on a phone, where there is no right button', () => {
		// The rows are declared once and rendered in both shapes; on a phone the press is the door
		// (a MenuButton with the same Button as its trigger), on a desktop the press is one more.
		expect(row).toMatch(/\{#snippet oCounterRows\(\)\}/);
		expect(row.match(/\{@render oCounterRows\(\)\}/g)).toHaveLength(2);
		const phone = row.slice(row.indexOf('{#if phoneWidth.yes}'), row.indexOf('{:else}'));
		expect(phone).toMatch(/<MenuButton label="O counter">/);
		expect(phone).not.toMatch(/onclick=\{counted\.more\}/);
		const desk = row.slice(row.indexOf('{:else}'), row.indexOf('{/if}', row.indexOf('{:else}')));
		expect(desk).toMatch(/onclick=\{counted\.more\}/);
	});
});
