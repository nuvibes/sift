/* The one list of keys, drawn in two places.
 *
 * Theater's panel and the Settings section are the same component, and the reason is recorded in
 * the file itself: two files would be two descriptions of one keyboard, free to disagree. What
 * that leaves worth pinning here is small and exact: a
 * row per declared shortcut, in the order it was handed them, with the key on one side and the
 * sentence on the other. A `<dl>` is what carries that pairing to a screen reader, and swapping the
 * two halves would still look right and read backwards.
 */

import { afterEach, beforeEach, expect, it } from 'vitest';
import { flushSync, mount } from 'svelte';
import KeyRows from './KeyRows.svelte';
import type { Shortcut } from '$lib/shell/shortcuts';

let host: HTMLDivElement;

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => host.remove());

function shortcut(id: string, shown: string, does: string): Shortcut {
	return { id, keys: [shown], shown, does, area: 'Anywhere' } as unknown as Shortcut;
}

function draw(shortcuts: readonly Shortcut[]) {
	mount(KeyRows, { target: host, props: { shortcuts } });
	flushSync();
}

it('draws the key and what it does as one pair, in that order', () => {
	draw([shortcut('app.search', 'Ctrl + F', 'Search your library')]);

	expect(host.querySelectorAll('dt')).toHaveLength(1);
	expect(host.querySelector('dt')?.textContent).toBe('Ctrl + F');
	expect(host.querySelector('dd')?.textContent).toBe('Search your library');
});

it('keeps the order it was handed rather than sorting', () => {
	draw([
		shortcut('one', '1', 'The first thing'),
		shortcut('two', '2', 'The second thing'),
		shortcut('three', '3', 'The third thing')
	]);

	expect([...host.querySelectorAll('dt')].map((each) => each.textContent)).toEqual(['1', '2', '3']);
	expect([...host.querySelectorAll('dd')].map((each) => each.textContent)).toEqual([
		'The first thing',
		'The second thing',
		'The third thing'
	]);
});

it('draws an empty list as an empty list rather than as nothing at all', () => {
	/* The wrapper belongs to the caller, so this component cannot decide that a group with no rows
	   should have no heading: `shortcutsByArea` already dropped those. What it must not do is
	   invent a row. */
	draw([]);

	expect(host.querySelector('dl')).not.toBeNull();
	expect(host.querySelectorAll('dt')).toHaveLength(0);
});
