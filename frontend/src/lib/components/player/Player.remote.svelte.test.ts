/*
 * The player answering the phone's presses for its bar and drawer.
 *
 * What would be silent if it broke: a press the Remote draws that the player does not answer (the
 * phone would say the screen never acted), a toggle from the phone flipping a setting the desk had
 * already set, the repeat landing on an answer other than the one the phone named, and the drawer
 * reported unlit while it is lit at the desk.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { abLoop } from '$lib/player/loop.svelte';
import { run } from '$lib/player/run.svelte';
import { commanded } from '$lib/shell/shortcuts';

vi.mock('$lib/player/playback', async () => {
	const real = await vi.importActual<typeof import('$lib/player/playback')>('$lib/player/playback');
	return {
		...real,
		planFor: async () => ({
			route: 'direct',
			reason: 'plays as it is',
			url: '/api/assets/asset-1/stream',
			scale_height: null,
			projected_realtime: null,
			streamable: true,
			duration_ms: 30_000,
			resume_ms: null
		}),
		attach: () => ({ detach: () => {} }),
		startAt: () => null
	};
});

vi.mock('$lib/api/client', () => ({
	api: { post: vi.fn(async () => ({})), get: vi.fn(async () => ({})), put: vi.fn(async () => ({})) }
}));

vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettingValues: vi.fn(async () => new Map<string, unknown>()),
	saveSettings: vi.fn(async () => {}),
	onSettingsSaved: vi.fn()
}));

import PlayerHarness from './PlayerHarness.svelte';
import type Player from './Player.svelte';

let host: HTMLElement;
let mounted: ReturnType<typeof mount> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
});

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
	abLoop.clear();
	if (run.shuffle) run.shuffle = false;
});

async function drawn(): Promise<ReturnType<typeof Player>> {
	host = document.createElement('div');
	document.body.append(host);
	let player: ReturnType<typeof Player> | null = null;
	mounted = mount(PlayerHarness, {
		target: host,
		props: { id: 'asset-1', held: (one: ReturnType<typeof Player>) => (player = one) }
	});
	flushSync();
	await vi.waitFor(() => expect(player).not.toBeNull());
	return player!;
}

describe("the player's answers to the Remote", () => {
	it('lands the repeat on the answer the phone named, by its place, and reports it', async () => {
		const offered = (await drawn()).remote();

		expect(commanded(offered.actions, 'player.repeat', 2)).toBe(true);
		flushSync();
		expect(offered.state().repeat).toBe('loop_one');
		expect(commanded(offered.actions, 'player.repeat', 0)).toBe(true);
		flushSync();
		expect(offered.state().repeat).toBe('once');
		// A place in no order is nobody's answer: refused, so the phone hears nothing happened.
		expect(commanded(offered.actions, 'player.repeat', 7)).toBe(false);
	});

	it('takes the shuffle the phone wants, leaving it where the desk already put it', async () => {
		const offered = (await drawn()).remote();

		expect(commanded(offered.actions, 'player.shuffle', 0)).toBe(true);
		expect(offered.state().shuffle).toBe(false);
		expect(commanded(offered.actions, 'player.shuffle', 1)).toBe(true);
		expect(commanded(offered.actions, 'player.shuffle', 1)).toBe(true);
		expect(offered.state().shuffle).toBe(true);
	});

	it('answers only what the drawer could press: no size for a file of one, no save without a loop', async () => {
		const offered = (await drawn()).remote();

		expect(offered.state().qualities).toEqual([]);
		expect(offered.state().loop_marks).toBe(0);
		expect(commanded(offered.actions, 'player.quality', 0)).toBe(false);
		expect(commanded(offered.actions, 'player.saveLoop', null)).toBe(false);
		// Nothing here can open a random file: a player drawn with nowhere to open one.
		expect(commanded(offered.actions, 'player.random', null)).toBe(false);
	});
});
