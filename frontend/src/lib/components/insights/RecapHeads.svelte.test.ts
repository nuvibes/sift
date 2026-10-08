/*
 * The recaps list with none in it: on the recaps screen, a page's empty state with its glyph and
 * title; in Insights' Recaps block, under that block's own heading, the sentence alone, carrying
 * what the title would have said.
 */
import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { words } from '$lib/design/testing.svelte';

vi.mock('$lib/library/recaps.svelte', () => ({
	recapShelf: { loaded: true, recaps: [], load: vi.fn(async () => {}) }
}));

import RecapHeads from './RecapHeads.svelte';

let drawn: Record<string, unknown> | null = null;

afterEach(() => {
	if (drawn) void unmount(drawn);
	drawn = null;
	document.body.innerHTML = '';
});

function draw(props: Record<string, unknown>): HTMLElement {
	drawn = mount(RecapHeads, { target: document.body, props }) as Record<string, unknown>;
	flushSync();
	return document.body;
}

it('draws a glyph and a title when it is the whole screen', () => {
	const shown = draw({ scope: 'page' });

	expect(shown.querySelector('.empty .glyph')).not.toBeNull();
	expect(words(shown.querySelector('.empty .title'))).toBe('No recaps yet');
	expect(words(shown.querySelector('.empty .said'))).toBe(
		'After a day, a week, a month or a year with enough viewing in it, its recap appears here.'
	);
});

it('says the whole of it in one sentence inside a block', () => {
	const shown = draw({ limit: 6 });

	expect(shown.querySelector('.empty .title')).toBeNull();
	expect(words(shown.querySelector('.empty .said'))).toMatch(
		/^No recaps yet\. After a day, a week/
	);
});
