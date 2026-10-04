/*
 * Every title under Organize wears a glyph: the one a screen names, or its queue's own. A detail
 * screen (a group, a may-be review) names its queue and no glyph, and wears the queue's.
 */
import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import codepoints from '$lib/generated/icon-codepoints.json';

vi.mock('$lib/organize/organize.svelte', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	heldBoard: {
		found: {
			queues: [
				{
					name: 'faces',
					title: 'Faces',
					icon: 'face',
					count: 3,
					band: 'decision',
					group: 'faces',
					group_title: 'Faces'
				},
				{
					name: 'known-people',
					title: 'People Sift can recognize',
					icon: 'supervised_user_circle',
					count: 40,
					band: 'decision',
					group: 'faces',
					group_title: 'Faces'
				}
			]
		},
		ensure: async () => {}
	}
}));

import OrganizeHeader from './OrganizeHeader.svelte';

let drawn: Record<string, unknown> | null = null;

afterEach(() => {
	if (drawn) void unmount(drawn);
	drawn = null;
	document.body.innerHTML = '';
});

function glyphOf(name: keyof typeof codepoints): string {
	return String.fromCodePoint(parseInt(codepoints[name], 16));
}

function titleGlyph(props: Record<string, unknown>): string | undefined {
	drawn = mount(OrganizeHeader, { target: document.body, props }) as Record<string, unknown>;
	flushSync();
	return document.body.querySelector('h1 .icon')?.textContent ?? undefined;
}

it("puts the queue's glyph beside a detail screen's title", () => {
	expect(titleGlyph({ queue: 'faces', here: '3 faces that may be Wren Halloway' })).toBe(
		glyphOf('face')
	);
});

it('keeps the glyph a screen names over its queue', () => {
	expect(titleGlyph({ queue: 'faces', here: 'Recent decisions', icon: 'history' })).toBe(
		glyphOf('history')
	);
});

it("counts what a search found on the lit tab, and the board's count on the others", () => {
	drawn = mount(OrganizeHeader, {
		target: document.body,
		props: { queue: 'known-people', litCount: 2 }
	}) as Record<string, unknown>;
	flushSync();

	const tabs = [...document.body.querySelectorAll('.tab-line a')].map((one) =>
		(one.textContent ?? '').replace(/\s+/g, ' ').trim()
	);
	expect(tabs).toEqual(['Faces3', 'People Sift can recognize2']);
});
