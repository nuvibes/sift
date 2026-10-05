import { afterEach, expect, it, vi } from 'vitest';
import { words as wordsOn } from '$lib/design/testing.svelte';
import { flushSync, mount, unmount } from 'svelte';

/*
 * POINTING AT A KEPT GROUP WASHES EVERY CELL, and letting go of it, by leaving, or by the row
 * going away under the pointer, takes the wash off.
 *
 * Every cell rather than one, because loading a group replaces the whole wall. The store is stood
 * in for: what is under test is the row's wiring, not the list's round trip.
 */

const { KEPT } = vi.hoisted(() => ({
	KEPT: { id: 'w1', name: 'Runway wall', layout: 'side_by_side', shape: null, cells: [] }
}));

vi.mock('$lib/theater/presets.svelte', () => {
	class NameTaken extends Error {}
	const presets = {
		items: [KEPT],
		ensure: vi.fn(async () => {}),
		reload: vi.fn(async () => {}),
		save: vi.fn(async (name: string) => ({ id: 'w2', name })),
		ask: vi.fn()
	};
	return { presets, NameTaken };
});

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(async () => ({ items: [], total: 0 })), post: vi.fn(async () => ({})) }
}));

import PresetsPanel from './PresetsPanel.svelte';
import { showing } from '$lib/theater/wall.svelte';
import { presets } from '$lib/theater/presets.svelte';

let host: HTMLElement;
let running: Record<string, unknown> | undefined;

afterEach(() => {
	if (running) void unmount(running);
	running = undefined;
	host?.remove();
	showing.wall = null;
});

function draw() {
	const wall = showing.ensure();
	host = document.createElement('div');
	document.body.append(host);
	running = mount(PresetsPanel, { target: host });
	flushSync();
	const row = host.querySelector('li');
	if (row === null) throw new Error('no kept group was drawn');
	return { wall, row };
}

it('washes every cell while a kept group is pointed at, and lets go on leaving', () => {
	const { wall, row } = draw();

	row.dispatchEvent(new MouseEvent('mouseenter'));
	expect(wall.aiming, 'pointing at a kept group marked nothing on the wall').toBe('every');

	row.dispatchEvent(new MouseEvent('mouseleave'));
	expect(wall.aiming).toBeNull();
});

it('lets go when the row is taken away under the pointer', () => {
	const { wall, row } = draw();
	row.dispatchEvent(new MouseEvent('mouseenter'));

	(presets as unknown as { items: unknown[] }).items = [];
	void unmount(running!);
	running = undefined;
	flushSync();

	expect(wall.aiming, 'the wash outlived the row that put it there').toBeNull();
	(presets as unknown as { items: unknown[] }).items = [KEPT];
});

/* Saving asks in the screen's dialog: the panel shuts when the pointer leaves for it. */
it('opens the save dialog for a new one', () => {
	draw();
	const open = [...host.querySelectorAll('button')].find((one) => wordsOn(one) === 'Save as new');
	open!.click();
	flushSync();

	expect(vi.mocked(presets.ask)).toHaveBeenCalledWith();
	expect(host.querySelector('input'), 'a name box opened inside the panel').toBeNull();
});
