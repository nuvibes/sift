/*
 * What the player REPORTS on its own: the sitting report. Facts, never conclusions:
 * `already_reported_ms: null` tells the server to judge a pass fresh.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';

/** The server's threshold, handed over, never computed here. */
const planFor = vi.fn(async (_id: string) => ({
	route: 'direct',
	reason: 'plays as it is',
	url: '/api/assets/asset-1/stream',
	scale_height: null,
	projected_realtime: null,
	streamable: true,
	duration_ms: 10_000,
	resume_ms: null,
	view_at_ms: 2_500
}));

vi.mock('$lib/player/playback', async () => {
	const real = await vi.importActual<typeof import('$lib/player/playback')>('$lib/player/playback');
	return {
		...real,
		planFor: (id: string) => planFor(id),
		attach: () => ({ detach: () => {} }),
		startAt: () => null
	};
});

/* Typed with its arguments, so `mock.calls` can be read. */
const post = vi.fn(async (_path: string, _options?: unknown) => ({}) as unknown);
vi.mock('$lib/api/client', () => ({
	api: {
		post: (path: string, options?: unknown) => post(path, options),
		get: vi.fn(async () => ({})),
		put: vi.fn(async () => ({}))
	}
}));

vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettingValues: vi.fn(
		async () => new Map<string, unknown>([['playback.loop_mode', 'loop_one']])
	),
	saveSettings: vi.fn(async () => {}),
	onSettingsSaved: vi.fn()
}));

import PlayerHarness from './PlayerHarness.svelte';
import { reactiveProps } from '$lib/design/testing.svelte';
import type { SittingPlace } from '$lib/player/sitting.svelte';

let host: HTMLElement;
let clock = 0;

beforeEach(() => {
	vi.clearAllMocks();
	/* NOT zero: `accumulate` reads a zero tick as none. */
	clock = 1_000;
	// Watch time is from the wall clock, so the clock is what these tests drive.
	vi.spyOn(performance, 'now').mockImplementation(() => clock);
});

afterEach(() => {
	vi.restoreAllMocks();
	host?.remove();
});

async function watching({ seconds = 0, place }: { seconds?: number; place?: SittingPlace } = {}) {
	host = document.createElement('div');
	document.body.append(host);
	mount(PlayerHarness, { target: host, props: { id: 'asset-1', place } });
	for (let turn = 0; turn < 8; turn++) {
		await Promise.resolve();
		flushSync();
	}
	const video = host.querySelector('video');
	if (!video) throw new Error('no video element; the player drew: ' + host.innerHTML.slice(0, 400));
	/* jsdom has no media pipeline, so a length is put on the element for the replay map. */
	if (seconds > 0) {
		Object.defineProperty(video, 'duration', { value: seconds, configurable: true });
		video.dispatchEvent(new Event('loadedmetadata'));
		flushSync();
	}
	video.dispatchEvent(new Event('play'));
	flushSync();
	return video;
}

/** Load-bearing: `sentMs` moves when a report LANDS. */
async function settle() {
	for (let turn = 0; turn < 6; turn++) {
		await Promise.resolve();
		flushSync();
	}
}

async function watch(video: HTMLVideoElement, ms: number) {
	clock += ms;
	video.dispatchEvent(new Event('timeupdate'));
	flushSync();
	await settle();
}

/** With the playhead moved, which places the time on the timeline. */
async function watchTo(video: HTMLVideoElement, ms: number, atSeconds: number) {
	clock += ms;
	video.currentTime = atSeconds;
	video.dispatchEvent(new Event('timeupdate'));
	flushSync();
	await settle();
}

function heatTotal(body: Record<string, unknown>): number {
	return Object.values((body.heat ?? {}) as Record<string, number>).reduce((a, b) => a + b, 0);
}

/** On repeat: jsdom never fires the second `play`, so the test does. */
async function playThrough(video: HTMLVideoElement) {
	video.dispatchEvent(new Event('ended'));
	flushSync();
	await settle();
	video.dispatchEvent(new Event('play'));
	flushSync();
}

function reports() {
	return post.mock.calls
		.filter((call) => String(call[0]).endsWith('/view'))
		.map((call) => (call[1] as { body: Record<string, unknown> }).body);
}

it("sends the leaving file's last piece at the next file's first frame, not in the gap", async () => {
	host = document.createElement('div');
	document.body.append(host);
	const props = reactiveProps({ id: 'asset-1' });
	mount(PlayerHarness, { target: host, props });
	await settle();
	await settle();
	const video = host.querySelector('video') as HTMLVideoElement;
	video.dispatchEvent(new Event('play'));
	flushSync();
	await watch(video, 3_000);
	const earlier = reports().length;

	props.id = 'asset-2';
	flushSync();
	await settle();
	expect(reports()).toHaveLength(earlier);

	video.dispatchEvent(new Event('playing'));
	flushSync();
	await settle();
	expect(reports()).toHaveLength(earlier + 1);
	const last = post.mock.calls.filter((call) => String(call[0]).endsWith('/view')).at(-1);
	expect(last?.[0]).toBe('/assets/asset-1/view');
});

it('reports the crossing once, and not again on every tick after it', async () => {
	const video = await watching();

	await watch(video, 3_000);
	await watch(video, 1_000);
	await watch(video, 1_000);

	// `announced` is raised before the request, or every tick in the air earns a view.
	expect(reports()).toHaveLength(1);
	expect(reports()[0].already_reported_ms).toBeNull();
});

it('starts a NEW pass when the file plays through, so a repeat is a second view', async () => {
	const video = await watching();

	await watch(video, 3_000);
	await playThrough(video);
	await watch(video, 3_000);

	/* The second pass says `already_reported_ms: null`, so it counts a second view. */
	const sent = reports();
	expect(sent).toHaveLength(3);
	expect(sent[1].already_reported_ms).toBe(3_000);
	expect(sent[2].already_reported_ms).toBeNull();
});

it('does NOT start a new pass when the closing report never landed', async () => {
	const video = await watching();

	await watch(video, 3_000);
	post.mockRejectedValueOnce(new Error('the network went'));
	await playThrough(video);
	await watch(video, 3_000);

	/* A failed request leaves the pass open: its time goes with the next piece. */
	const sent = reports();
	expect(sent[sent.length - 1].already_reported_ms).toBe(3_000);
});

it('puts the time on the part of the timeline the playhead actually crossed', async () => {
	const video = await watching({ seconds: 10 });

	await watchTo(video, 3_000, 3);

	/* The time landed on the slices the playhead crossed and no others. */
	const map = (reports()[0].heat ?? {}) as Record<string, number>;
	const slices = Object.keys(map).map(Number);
	expect(slices.length).toBeGreaterThan(1);
	expect(Math.min(...slices)).toBe(0);
	expect(Math.max(...slices)).toBeLessThan(30);
	expect(heatTotal(reports()[0])).toBeCloseTo(3_000, -2);
});

it('takes off exactly what it sent, so a tick that lands mid-request is not thrown away', async () => {
	/* A tick while a report is in the air: taking off exactly what went keeps it. */
	const video = await watching({ seconds: 10 });

	let land: (() => void) | null = null;
	post.mockImplementationOnce(async () => {
		await new Promise<void>((ready) => (land = ready));
		return {};
	});

	await watchTo(video, 3_000, 3);
	expect(reports()).toHaveLength(1);
	const sent = heatTotal(reports()[0]);
	expect(sent).toBeGreaterThan(0);

	clock += 1_000;
	video.currentTime = 4;
	video.dispatchEvent(new Event('timeupdate'));
	flushSync();

	if (!land) throw new Error('the report was never issued, so nothing was held open');
	(land as () => void)();
	await settle();

	await playThrough(video);

	const closing = reports()[reports().length - 1];
	expect(
		heatTotal(closing),
		'the second of watching that landed while the first report was in the air was thrown away'
	).toBeCloseTo(1_000, -2);
	expect(heatTotal(closing)).toBeLessThan(sent);
});

it('names the sitting, and every piece of it carries the same name', async () => {
	/* One sitting id across pieces; a repeat does not re-mint it. */
	const video = await watching();

	await watch(video, 3_000);
	await playThrough(video);
	await watch(video, 3_000);

	const names = reports().map((body) => body.sitting);
	expect(names[0]).toEqual(expect.any(String));
	expect(new Set(names).size).toBe(1);
});

it('says where the sitting is happening on every piece of it', async () => {
	const place = { screen: 'corner' } as const;
	const video = await watching({ place });

	await watch(video, 3_000);
	await playThrough(video);
	await watch(video, 3_000);

	expect(reports().length).toBeGreaterThan(1);
	for (const body of reports()) expect(body.screen).toBe('corner');
});

it('counts the jumps, and charges each one to the piece it happened in', async () => {
	/* A jump is a playhead moving much further than the time passed. */
	const video = await watching({ seconds: 10 });

	await watchTo(video, 3_000, 3);
	await watchTo(video, 250, 9.5);
	await watchTo(video, 1_000, 9.6);
	await playThrough(video);

	const sent = reports();
	// Charged per PIECE.
	expect(sent[0].seeks).toBe(0);
	expect(sent[1].seeks).toBe(1);
});

it('keeps each jump as where it was and where it went, and where the sitting began', async () => {
	const video = await watching({ seconds: 10 });

	await watchTo(video, 3_000, 3);
	await watchTo(video, 250, 9.5);
	await playThrough(video);

	const sent = reports();
	expect(sent.every((body) => body.start_ms === 0)).toBe(true);
	expect(sent[0].seek_log).toEqual([]);
	expect(sent[1].seek_log).toEqual([{ from_ms: 3_000, to_ms: 9_500 }]);
});

it('counts every pass through the end under Repeat, each on the piece it closed', async () => {
	const video = await watching();

	await watch(video, 3_000);
	await playThrough(video);
	await watch(video, 3_000);
	await playThrough(video);
	await playThrough(video);

	const passes = reports().map((body) => Number(body.completions ?? 0));
	expect(passes.reduce((a, b) => a + b, 0)).toBe(3);
});

it('keeps the passes a piece that never landed carried, for the next piece', async () => {
	const video = await watching();
	await watch(video, 3_000);

	post.mockRejectedValueOnce(new Error('offline'));
	await playThrough(video);
	await playThrough(video);

	expect(reports().at(-1)?.completions).toBe(2);
});

it('keeps the time at the speed it was played at', async () => {
	const video = await watching();
	video.playbackRate = 1.5;

	await watch(video, 3_000);

	expect(reports()[0].speeds).toEqual({ '1.5': 3_000 });
	expect(reports()[0].fullscreen_ms).toBe(0);
});

it('does not read a pause as a jump', async () => {
	/* A pause moves the playhead by nothing: not a seek. */
	const video = await watching({ seconds: 10 });

	await watchTo(video, 1_000, 1);
	await watchTo(video, 5_000, 1);
	await playThrough(video);

	expect(reports().every((body) => body.seeks === 0)).toBe(true);
});
