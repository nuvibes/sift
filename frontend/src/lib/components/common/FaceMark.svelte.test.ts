/*
 * The mark on a face Sift named on its own and took as a reference.
 *
 * It wears the glyph History gives what Sift took from a face, read from the one table, and says the
 * caller's sentence, so the face does not read as one somebody confirmed.
 */
import { readFileSync } from 'node:fs';

import { afterEach, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import FaceMark from './FaceMark.svelte';
import { facetValueIcon } from '$lib/components/shell/facet-labels';

const source = readFileSync('src/lib/components/common/FaceMark.svelte', 'utf8');

let drawn: Record<string, unknown> | null = null;
let host: HTMLElement | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
});

function draw(props: { kind: 'learned' | 'reference'; label: string }): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FaceMark, { target: host, props }) as Record<string, unknown>;
	flushSync();
	return host;
}

it("wears History's mark for a face and says the caller's sentence", () => {
	const said = 'Sift recognized Wren in this one and uses it to identify them';
	const icon = draw({ kind: 'learned', label: said }).querySelector('.icon');
	expect(icon?.getAttribute('aria-label')).toBe(said);
	// The glyph is read from the table History's rows read, never a second copy of its name.
	expect(facetValueIcon('enriched', 'faces')).toBe('familiar_face_and_zone');
	expect(source).toContain("learned: facetValueIcon('enriched', 'faces')");
});

it('keeps the plain face glyph for a reference somebody confirmed', () => {
	expect(source).toContain("reference: 'face',");
});

it('draws the glyph in the accent with nothing filled behind it', () => {
	const style = source.slice(source.indexOf('<style>'));
	expect(style).toContain('color: var(--sift-accent-text);');
	expect(style).not.toMatch(/background|border-radius/);
});
