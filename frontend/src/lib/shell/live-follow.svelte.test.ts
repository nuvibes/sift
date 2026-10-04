/*
 * The stores that read from the server and keep the answer are told when it moves.
 *
 * Each of these reads once and keeps what it read, so without being told a change made in another
 * window (a tunnel started, a Theater wall saved, a folder added, a release found, an answer given
 * about the interface) would stay invisible here until the page was reloaded. Each case rings the bell the
 * server rings for its subject and asserts the store asks again; the negative half asserts a store
 * that was never read asks nothing, which is what keeps a bell from costing a read per session.
 */
import { flushSync } from 'svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { libraryChanges, mine, settingChanges } from '$lib/library/changes.svelte';
import { movable } from '$lib/library/movable.svelte';
import { presets } from '$lib/theater/presets.svelte';
import { updates } from '$lib/shell/updates.svelte';
import { recallInterfaceState, chipRemoveSkipped } from '$lib/shell/interface-state.svelte';
import { Tunnels } from '$lib/settings-ui/tunnels-state.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {}
}));

const mocked = vi.mocked(api);

function asked(path: string): number {
	return mocked.get.mock.calls.filter(([one]) => one === path).length;
}

beforeEach(() => {
	mocked.get.mockReset();
	mocked.get.mockImplementation(async (path: string) => {
		if (path === '/library/roots') return { roots: [] };
		if (path === '/library/folders') return { folders: [] };
		if (path === '/theater/arrangements') return { items: [] };
		if (path === '/update/check') return { update_available: false };
		if (path === '/settings/interface') return { state: {} };
		if (path === '/tunnels') return [];
		if (path === '/download-routes') return { default: 'direct', sites: {}, available: [] };
		return {};
	});
});

async function settle(): Promise<void> {
	for (let turn = 0; turn < 5; turn += 1) await Promise.resolve();
}

describe('a store that outlives every screen', () => {
	it('reads the folders again on the library bell, once it has read them', async () => {
		libraryChanges.changed();
		await settle();
		expect(asked('/library/folders')).toBe(0);

		await movable.ensure();
		libraryChanges.changed();
		await settle();

		expect(asked('/library/folders')).toBe(2);
	});

	it('reads the Theater walls again on the mine bell, once it has read them', async () => {
		await presets.ensure();
		mine.changed();
		await settle();

		expect(asked('/theater/arrangements')).toBe(2);
	});

	it('reads the update check again on the settings bell, once it has read it', async () => {
		await updates.load();
		settingChanges.changed();
		await settle();

		expect(asked('/update/check')).toBe(2);
	});

	it('takes up an answer about the interface given in another window', async () => {
		await recallInterfaceState();
		expect(chipRemoveSkipped()).toBe(false);
		mocked.get.mockImplementation(async () => ({ state: { 'confirm.chip_remove': 'skip' } }));

		settingChanges.changed();
		await settle();

		expect(chipRemoveSkipped()).toBe(true);
	});
});

describe('a store a screen holds', () => {
	it('reads the tunnels again on the settings bell while the screen that follows is open', async () => {
		const tunnels = new Tunnels();
		const stop = $effect.root(() => tunnels.follow());
		flushSync();
		settingChanges.changed();
		flushSync();
		await settle();
		expect(asked('/tunnels')).toBe(1);

		stop();
		settingChanges.changed();
		flushSync();
		await settle();
		expect(asked('/tunnels')).toBe(1);
	});
});
