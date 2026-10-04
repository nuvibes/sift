/*
 * The heart, the stars and the stash-box marks on an entity's page stand at one even spacing.
 *
 * The heart and the stars are controls, each a glyph in a box with a step of room either side; the
 * marks are not controls, and with no box of their own the row would space them unevenly against
 * the heart and the star. In a row of controls a mark takes a
 * control's box (`padded`), and the row's gap is the marks' own, so every glyph is the same distance
 * from the next. Read off the stylesheets, because jsdom lays nothing out.
 */
import { afterEach, beforeEach, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import EnrichmentMarks from './EnrichmentMarks.svelte';
import marksSource from './EnrichmentMarks.svelte?raw';
import headerSource from './EntityAbout.svelte?raw';
import heartSource from '$lib/components/common/Heart.svelte?raw';
import ratingSource from '$lib/components/common/RatingChip.svelte?raw';
import generated from '$lib/generated/icon-codepoints.json';

/** The character the icon font draws for one glyph name, which is what `Icon` renders. */
const codepoints = generated as Record<string, string>;

/** The value one rule of a stylesheet gives one property, or null where the rule does not. */
function declared(source: string, selector: string, property: string): string | null {
	const style = source.slice(source.indexOf('<style>'));
	const at = style.indexOf(`${selector} {`);
	if (at === -1) return null;
	const body = style.slice(at, style.indexOf('}', at));
	return body.match(new RegExp(`\\n\\s*${property}:\\s*([^;]+);`))?.[1] ?? null;
}

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host.remove();
});

it("gives a padded mark the heart's box and the star's box, to the token", () => {
	const heart = declared(heartSource, '.heart', 'padding');
	expect(heart).toBe('var(--space-1) var(--space-2)');
	expect(declared(marksSource, '.padded .mark', 'padding')).toBe(heart);
	expect(ratingSource).toContain(`padding: ${heart};`);
});

it("spaces the header's row by the marks' own step, so every glyph is one distance apart", () => {
	const step = declared(marksSource, '.marks', 'gap');
	expect(step).toBe('var(--space-1)');
	expect(declared(headerSource, '.opinions', 'gap')).toBe(step);
	expect(headerSource).toMatch(/<EnrichmentMarks\s+padded\s/);
});

it('draws the padded marks at the glyph size of the controls beside them', () => {
	drawn = mount(EnrichmentMarks, {
		target: host,
		props: { sources: [{ via: 'stash', name: 'Example box', box: null }], padded: true }
	}) as Record<string, unknown>;
	flushSync();
	expect(host.querySelector('.marks')?.classList.contains('padded')).toBe(true);
	expect(host.querySelector('.mark .icon')?.classList.contains('size-20')).toBe(true);
});

it('draws the Created by mark in the glyph and words of the act that made the row', () => {
	// A tag Sift put on a file it compressed wears Compress; one on a file it edited wears Modify's
	// drafting tools. With no act stored the pass's own general mark and words stay.
	for (const [act, glyph, said] of [
		['compress', 'compress', 'Created by Sift: from a file it compressed'],
		['edit', 'design_services', 'Created by Sift: from a file it edited'],
		[null, 'auto_fix_high', 'Created by Sift: from a file it made']
	] as const) {
		drawn = mount(EnrichmentMarks, {
			target: host,
			props: { said: 'created', sources: [{ via: 'produced', act, name: null, box: null }] }
		}) as Record<string, unknown>;
		flushSync();
		const mark = host.querySelector('.mark');
		expect(mark?.querySelector('.icon')?.textContent).toBe(
			String.fromCodePoint(parseInt(codepoints[glyph], 16))
		);
		expect(mark?.getAttribute('aria-label')).toBe(said);
		unmount(drawn);
		drawn = null;
	}
});
