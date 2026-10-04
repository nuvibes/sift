/* A run moving on by itself keeps the element somebody pressed play on.
 *
 * Safari lets a video start with its sound on by itself only on an element somebody has already
 * pressed play on, and a run moving on (Play through, Shuffle or not) asks for the next file after
 * an await, outside any press. A new element for every file would land paused on every one of them.
 * These drive the end of a file and ask for the next file playing in the SAME element.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { abLoop } from '$lib/player/loop.svelte';
import { run } from '$lib/player/run.svelte';

const planFor = vi.fn(async (id: string) => ({
	route: 'direct',
	reason: 'plays as it is',
	url: `/api/assets/${id}/stream`,
	scale_height: null,
	projected_realtime: null,
	streamable: true,
	duration_ms: 3_000 as number | null,
	resume_ms: null,
	qualities: []
}));

vi.mock('$lib/player/playback', async () => {
	const real = await vi.importActual<typeof import('$lib/player/playback')>('$lib/player/playback');
	return { ...real, planFor: (id: string) => planFor(id), startAt: () => null };
});

vi.mock('$lib/api/client', () => ({
	api: { post: vi.fn(async () => ({})), get: vi.fn(async () => ({})), put: vi.fn(async () => ({})) }
}));

vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettingValues: vi.fn(
		async () => new Map<string, unknown>([['playback.loop_mode', 'loop_all']])
	),
	saveSettings: vi.fn(async () => {}),
	onSettingsSaved: vi.fn()
}));

import PlayerHarness from './PlayerHarness.svelte';

let host: HTMLElement;
let mounted: ReturnType<typeof mount> | null = null;
const played: { element: HTMLMediaElement; src: string }[] = [];

beforeEach(() => {
	played.length = 0;
	vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (
		this: HTMLMediaElement
	) {
		played.push({ element: this, src: this.getAttribute('src') ?? '' });
		return Promise.resolve();
	});
	vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined);
});

afterEach(() => {
	vi.restoreAllMocks();
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
	abLoop.clear();
	if (run.shuffle) run.toggle();
});

async function settle() {
	for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
	flushSync();
}

describe('the end of a file in a run', () => {
	it('plays the next file in the same element, which is the one a press let play', async () => {
		if (!run.shuffle) run.toggle();
		const next = ['asset-2', 'asset-3'];
		const props = $state({
			id: 'asset-1',
			onplayedthrough: () => {
				props.id = next.shift() ?? 'asset-1';
			}
		});
		host = document.createElement('div');
		document.body.append(host);
		mounted = mount(PlayerHarness, { target: host, props });
		await settle();

		const first = host.querySelector('video');
		expect(first).not.toBeNull();
		first!.dispatchEvent(new Event('loadedmetadata'));

		for (const id of ['asset-2', 'asset-3']) {
			const now = host.querySelector('video')!;
			now.dispatchEvent(new Event('ended'));
			flushSync();
			// Between the end and the next plan the element is still on the stage.
			expect(host.querySelector('video')).toBe(first);
			await settle();
			const after = host.querySelector('video')!;
			expect(after).toBe(first);
			expect(after.getAttribute('src')).toBe(`/api/assets/${id}/stream`);
			after.dispatchEvent(new Event('loadedmetadata'));
			expect(played.at(-1)).toEqual({ element: first, src: `/api/assets/${id}/stream` });
		}
	});

	it('takes the element away when the next file cannot be played here', async () => {
		const props = $state({ id: 'asset-1', onplayedthrough: () => (props.id = 'asset-9') });
		host = document.createElement('div');
		document.body.append(host);
		mounted = mount(PlayerHarness, { target: host, props });
		await settle();
		planFor.mockImplementationOnce(async (id: string) => ({
			route: 'unread',
			reason: 'Sift has not read this file yet.',
			url: `/api/assets/${id}/stream`,
			scale_height: null,
			projected_realtime: null,
			streamable: false,
			duration_ms: null,
			resume_ms: null,
			qualities: []
		}));
		host.querySelector('video')!.dispatchEvent(new Event('ended'));
		flushSync();
		await settle();
		expect(host.querySelector('video')).toBeNull();
	});
});
