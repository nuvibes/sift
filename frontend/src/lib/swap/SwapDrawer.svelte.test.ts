/*
 * The drawer's picks are the chips the player's Enrichment rows draw: the entity's picture, a link
 * to its page, the hover card around it. A file's chip opens the file. And the way out says both
 * things it does.
 */
import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async () => []),
		// The weigh answers as the server does: a figure, and nothing left out.
		post: vi.fn(async (path: string) =>
			path === '/swap/weigh' ? { files: 1, bytes: 1, left_out: [], left_out_other: 0 } : {}
		)
	},
	ApiError: class extends Error {}
}));

import SwapDrawer from './SwapDrawer.svelte';
import { swapMode } from './mode.svelte';
import { api } from '$lib/api/client';

let drawn: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	swapMode.leave();
	document.body.innerHTML = '';
});

function draw() {
	swapMode.enter();
	swapMode.toggle({ kind: 'person', id: 'p1', name: 'Ava Example' });
	swapMode.toggle({ kind: 'tag', id: 't1', name: 'beach' });
	swapMode.toggle({ kind: 'asset', id: 'a1', name: 'harbor-walk.mp4' });
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(SwapDrawer, { target: host });
	flushSync();
}

const link = (name: string) =>
	[...document.querySelectorAll<HTMLAnchorElement>('a')].find((one) =>
		one.textContent?.includes(name)
	);

it('draws each entity as the Enrichment chip: its page, its picture, the hover card', () => {
	draw();
	const person = link('Ava Example');
	expect(person?.getAttribute('href')).toBe('/people/p1');
	expect(person?.closest('.hovered'), 'no hover card around the chip').not.toBeNull();
	expect(person?.querySelector('img, .avatar, [class*="shot"]'), 'no picture').not.toBeNull();
	expect(link('beach')?.getAttribute('href')).toBe('/tags/t1');
});

it('opens a file from its chip', () => {
	draw();
	expect(link('harbor-walk.mp4')?.getAttribute('href')).toBe('/asset/a1');
});

it('still takes a pick out from its chip', () => {
	draw();
	const out = [...document.querySelectorAll('button')].find((one) =>
		one.getAttribute('aria-label')?.includes('Ava Example')
	);
	out?.click();
	flushSync();
	expect(swapMode.has('person', 'p1')).toBe(false);
});

it('says the way out deselects everything too', () => {
	draw();
	expect(document.body.textContent).toContain('Deselect all and leave swap mode');
	expect(document.body.textContent).not.toMatch(/(^|[^a-z])Leave swap mode/);
});

it('asks nothing of the server while swap mode has never been entered', () => {
	/* Mounted on every page, shut: its doors must not ask the tunnels and the download folders at
	   each load, and a guest (who cannot swap) must not be refused in the console. */
	vi.mocked(api.get).mockClear();
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(SwapDrawer, { target: host });
	flushSync();
	expect(vi.mocked(api.get).mock.calls.map(([path]) => path)).toEqual([]);
	expect(document.body.textContent).not.toContain('Start the swap');

	swapMode.enter();
	flushSync();
	expect(document.body.textContent).toContain('Start the swap');
	swapMode.leave();
	flushSync();
	// Kept once drawn, so the drawer closes with its doors still in it.
	expect(document.body.textContent).toContain('Start the swap');
});

it('draws each pick under the name it has now, not the one it was picked under', async () => {
	/* A pick keeps its id and a copy of its name, and the copy lives as long as the tab: a tag
	   renamed on its own page would go on reading its old name here. The drawer asks what each is
	   called now and draws that. */
	vi.mocked(api.get).mockImplementation(async (path: string) =>
		path === '/search/names-now' ? { items: [{ id: 't1', name: 'quayside' }] } : []
	);
	draw();
	for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
	flushSync();
	expect(link('quayside')?.getAttribute('href')).toBe('/tags/t1');
	expect(link('beach')).toBeUndefined();
	vi.mocked(api.get).mockImplementation(async () => []);
});

it('weighs one pick once and reads the tunnels once when it opens', async () => {
	/* `Tabs` draws every pane of its row, so a form in each of Send, Receive and Exchange would each
	   weigh the picks and read the tunnels. Only the pane on show is drawn, and it asks once. */
	vi.useFakeTimers();
	try {
		vi.mocked(api.get).mockClear();
		vi.mocked(api.post).mockClear();
		swapMode.enter();
		const host = document.createElement('div');
		document.body.append(host);
		drawn = mount(SwapDrawer, { target: host });
		flushSync();
		await vi.advanceTimersByTimeAsync(10);
		swapMode.toggle({ kind: 'person', id: 'p1', name: 'Ava Example' });
		flushSync();
		await vi.advanceTimersByTimeAsync(400);
		flushSync();
		const gets = vi.mocked(api.get).mock.calls.map(([path]) => path);
		const posts = vi.mocked(api.post).mock.calls.map(([path]) => path);
		expect(gets.filter((path) => path === '/swap/tunnels')).toHaveLength(1);
		expect(posts.filter((path) => path === '/swap/weigh')).toHaveLength(1);
	} finally {
		vi.useRealTimers();
	}
});
