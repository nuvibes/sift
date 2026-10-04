/*
 * The headings inside a page are one scale: a title, a group's heading, a subheading and a band,
 * each one rung in one face.
 *
 * Two headings stand on the subheading's rung: a subheading over a block inside a group
 * (Performance's "Your network shares") and a form card's title (Stash-boxes' "Add a stash-box").
 * Written separately they drift into two faces at one size. One token says the rung, and both
 * headings wear it.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

/** A file of the client, read off the disk: a stylesheet asked for as `?raw` arrives empty here. */
const read = (path: string) => readFileSync(resolve('src', path), 'utf8');

/** The declarations of every rule whose whole selector is `selector` in a component's style. */
function rule(source: string, selector: string): string {
	const style = source.slice(source.indexOf('<style')).replace(/\/\*[\s\S]*?\*\//g, '');
	return [...style.matchAll(/([^{}]+)\{([^}]*)\}/g)]
		.filter((one) => one[1].trim() === selector)
		.map((one) => one[2])
		.join(';');
}

describe('the subheading rung', () => {
	it('is one token in the display face, at the size of the body', () => {
		const css = read('app.css');
		const h3 = css.match(/--text-h3:\s*([^;]+);/)?.[1] ?? '';
		const body = css.match(/--text-body:\s*([^;]+);/)?.[1] ?? '';
		expect(h3, 'the rung is not a token').toContain('var(--font-display)');
		// The body's size and leading, so the rung sits one step under a group's heading.
		expect(h3.split(' ')[1]).toBe(body.split(' ')[1]);
	});

	it("is what a subheading and a form card's title both wear", () => {
		expect(rule(read('lib/components/common/SectionHeading.svelte'), 'h3')).toContain(
			'font: var(--text-h3)'
		);
		expect(
			rule(read('lib/components/common/FormCard.svelte'), 'h3'),
			"a form card's title in a face of its own"
		).toContain('font: var(--text-h3)');
	});
});
