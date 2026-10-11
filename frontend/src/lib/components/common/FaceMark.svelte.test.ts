/*
 * The mark on a face Sift named on its own, a reference or not: one glyph, the one History gives
 * what Sift took from a face, read from the one table. A reference is said in its words only.
 */
import { readFileSync } from 'node:fs';

import { afterEach, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import FaceMark from './FaceMark.svelte';
import { facetValueIcon } from '$lib/components/shell/facet-labels';
import codepoints from '$lib/generated/icon-codepoints.json';

const source = readFileSync('src/lib/components/common/FaceMark.svelte', 'utf8');

let drawn: Record<string, unknown> | null = null;
let host: HTMLElement | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

function draw(props: { kind: 'asking' | 'recognized'; label?: string }): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FaceMark, { target: host, props }) as Record<string, unknown>;
	flushSync();
	return host;
}

it("wears History's mark for a face Sift named, read from the one table", () => {
	const icon = draw({ kind: 'recognized' }).querySelector('.icon');
	expect(icon?.getAttribute('aria-label')).toBe('Recognized by Sift');
	// The glyph is read from the table History's rows read, never a second copy of its name.
	expect(facetValueIcon('enriched', 'faces')).toBe('familiar_face_and_zone');
	expect(source).toContain("recognized: facetValueIcon('enriched', 'faces')");
	expect(source, 'the wand is back on a face').not.toContain('auto_fix_high');
});

it("says the caller's sentence on the same glyph for a face Sift also identifies others by", () => {
	const said = 'Sift recognized Wren in this one and uses it to identify them';
	const icon = draw({ kind: 'recognized', label: said }).querySelector('.icon');
	expect(icon?.getAttribute('aria-label')).toBe(said);
	expect(icon?.textContent).toBe(
		String.fromCodePoint(parseInt(codepoints.familiar_face_and_zone, 16))
	);
});

it('draws the glyph in the accent with nothing filled behind it', () => {
	const style = source.slice(source.indexOf('<style>'));
	expect(style).toContain('color: var(--sift-accent-text);');
	expect(style).not.toMatch(/background|border-radius/);
});
