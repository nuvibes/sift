/* What the player REPORTS.
 *
 * The other tests of this component cover its chrome; a request it makes is what a chrome test
 * cannot see, so a route can be wrong while every one of them passes. These are about the one
 * write the player makes on its own, without anybody pressing
 * anything: the sitting report.
 *
 * Facts, never conclusions. Whether something was a view is the server's judgement: three screens
 * send this report and a rule living in one of them is not a rule, so what is asserted here is
 * what the client SAYS: how long, and how much of this pass it had already said. The second is the
 * whole of it, because `already_reported_ms: null` is the client telling the server "judge this
 * fresh", and that is what makes a second playthrough a second view.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';

/** A ten second clip whose threshold is two and a half seconds: a quarter of it, as the server
 *  works out. Handed over rather than computed here: a browser deriving its own threshold would be
 *  a second copy of a rule that exists to have one. */
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

/* Typed with the arguments it is called with, not as a bare `vi.fn()`.
 *
 * A double declared with no parameters gives `mock.calls` the type `[][]`, so every read of an
 * argument is an error, and the test that reads what was SENT is the only reason this file
 * exists. It is also the one shape a Svelte component's own doubles never catch: a double inside a
 * `.svelte` file is not type-checked, and this one is. */
const post = vi.fn(async (_path: string, _options?: unknown) => ({}) as unknown);
vi.mock('$lib/api/client', () => ({
	api: {
		post: (path: string, options?: unknown) => post(path, options),
		get: vi.fn(async () => ({})),
		put: vi.fn(async () => ({}))
	}
}));

vi.mock('$lib/settings-ui/settings', () => ({
	// Repeat, which is what the question is about: the file plays again rather than moving on.
	fetchSettingValues: vi.fn(
		async () => new Map<string, unknown>([['playback.loop_mode', 'loop_one']])
	),
	saveSettings: vi.fn(async () => {}),
	onSettingsSaved: vi.fn()
}));

import PlayerHarness from './PlayerHarness.svelte';
import type { SittingPlace } from '$lib/player/sitting.svelte';

let host: HTMLElement;
let clock = 0;

beforeEach(() => {
	vi.clearAllMocks();
	/* NOT zero. `accumulate` guards with `if (!lastTick) return`, so a first tick stamped at zero
	   reads as no tick at all and the whole sitting measures nothing. It cannot happen in a browser
	   (`performance.now()` at the moment somebody presses play is never zero) but a fake clock
	   starting there measures a sitting of nought and every assertion below passes vacuously. */
	clock = 1_000;
	// The player measures watch time from the wall clock rather than from the playhead, because
	// somebody who dragged the scrubber to the end has not watched anything. So the clock is what
	// these tests drive.
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
	/* Only the tests about the replay map ask for this, and they have to.
	 *
	 * jsdom has no media pipeline, so `video.duration` is NaN and the player's `duration` stays at
	 * nought, which is the right answer for it, and it means `spreadHeat` returns at its first
	 * line and the map is never filled. A length has to be put on the element and announced the way
	 * a browser announces it. Everything above this line measures WATCH TIME, which is taken off
	 * the wall clock and needs no length at all. */
	if (seconds > 0) {
		Object.defineProperty(video, 'duration', { value: seconds, configurable: true });
		video.dispatchEvent(new Event('loadedmetadata'));
		flushSync();
	}
	video.dispatchEvent(new Event('play'));
	flushSync();
	return video;
}

/** Let every request in the air come back, which in a browser happens between ticks.
 *
 * Load-bearing rather than tidying. `sentMs` moves when a report LANDS, not when it is sent, so a
 * test that fires the next event in the same turn builds it while the first is still outstanding,
 * and reads `already_reported_ms: null` on a piece that is nothing of the kind. */
async function settle() {
	for (let turn = 0; turn < 6; turn++) {
		await Promise.resolve();
		flushSync();
	}
}

/** Let `ms` of watching pass, and let the player notice, the way the element makes it notice. */
async function watch(video: HTMLVideoElement, ms: number) {
	clock += ms;
	video.dispatchEvent(new Event('timeupdate'));
	flushSync();
	await settle();
}

/** The same, with the playhead moved to where it got to, which is what places the time on the
 *  timeline. Without it every stretch is charged to the same slice and the map says one thing. */
async function watchTo(video: HTMLVideoElement, ms: number, atSeconds: number) {
	clock += ms;
	video.currentTime = atSeconds;
	video.dispatchEvent(new Event('timeupdate'));
	flushSync();
	await settle();
}

/** How much time a report put on the timeline, across every slice. */
function heatTotal(body: Record<string, unknown>): number {
	return Object.values((body.heat ?? {}) as Record<string, number>).reduce((a, b) => a + b, 0);
}

/**
 * The file reaches its end and, on repeat, starts again.
 *
 * The second event matters: reaching the end pauses the element, so the rewind is followed by a
 * real `play`, which starts the next stretch of measured time. jsdom has no media pipeline and
 * `video.play()` throws there, so nothing fires it on its own, and a test leaving it out would
 * measure nothing after the first pass and read that as the reset failing.
 */
async function playThrough(video: HTMLVideoElement) {
	video.dispatchEvent(new Event('ended'));
	flushSync();
	await settle();
	video.dispatchEvent(new Event('play'));
	flushSync();
}

/** Every sitting report, in order. */
function reports() {
	return post.mock.calls
		.filter((call) => String(call[0]).endsWith('/view'))
		.map((call) => (call[1] as { body: Record<string, unknown> }).body);
}

it('reports the crossing once, and not again on every tick after it', async () => {
	const video = await watching();

	await watch(video, 3_000);
	await watch(video, 1_000);
	await watch(video, 1_000);

	// One report. `announced` is raised BEFORE the request rather than when it lands, or the three
	// or four ticks that fire while it is in the air would each earn a view of their own.
	expect(reports()).toHaveLength(1);
	expect(reports()[0].already_reported_ms).toBeNull();
});

it('starts a NEW pass when the file plays through, so a repeat is a second view', async () => {
	const video = await watching();

	await watch(video, 3_000);
	await playThrough(video);
	await watch(video, 3_000);

	/* Three reports: the first pass crossing, the first pass closing, and the second pass crossing.
	 * The last is the one that matters: `already_reported_ms: null` says "this pass has told you
	 * nothing", which is what makes the server judge it fresh and count a second view. Without the
	 * pass reset it would say 6000, and forty-six loops of a ten second clip would be one view. */
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

	/* The honest way to fail. Nothing resets unless the request landed, so the pass stays open: its
	 * time is still owed, goes with the next piece, and `already_reported_ms` still describes it:
	 * the server reads one continuing sitting and counts ONE. Resetting first would either lose the
	 * pass's time or invent a view for a report nobody received. */
	const sent = reports();
	expect(sent[sent.length - 1].already_reported_ms).toBe(3_000);
});

/*
 * The replay map, the other half of what this report carries.
 *
 * Time watched is one number; where it was watched is a hundred, and the curve under the scrubber
 * is drawn from them. `ReplayCurve.svelte.test.ts` draws a curve it is handed; this checks one
 * being reported.
 */

it('puts the time on the part of the timeline the playhead actually crossed', async () => {
	const video = await watching({ seconds: 10 });

	await watchTo(video, 3_000, 3);

	/* A hundred slices over ten seconds is a slice per tenth, and three seconds of watching from the
	 * start crossed the first thirty of them. The point is not the exact figures: it is that the
	 * time landed on the slices the playhead went through and on no others, which is what makes the
	 * curve a history rather than a decoration. */
	const map = (reports()[0].heat ?? {}) as Record<string, number>;
	const slices = Object.keys(map).map(Number);
	expect(slices.length).toBeGreaterThan(1);
	expect(Math.min(...slices)).toBe(0);
	expect(Math.max(...slices)).toBeLessThan(30);
	expect(heatTotal(reports()[0])).toBeCloseTo(3_000, -2);
});

it('takes off exactly what it sent, so a tick that lands mid-request is not thrown away', async () => {
	/*
	 * THE ONE SITUATION THE TWO ANSWERS DIFFER IN, arranged on purpose.
	 *
	 * `onTimeUpdate` fires several times a second and the report is a request, so a tick can add to
	 * a slice while one is in the air. Emptying the map when it lands throws that tick away; taking
	 * off exactly what went keeps it. Every other arrangement (send, wait, send again) gives the
	 * same answer either way, which is why this holds the request open and ticks underneath it.
	 */
	const video = await watching({ seconds: 10 });

	let land: (() => void) | null = null;
	post.mockImplementationOnce(async () => {
		await new Promise<void>((ready) => (land = ready));
		return {};
	});

	// Crosses the threshold, so the first report goes, and stays in the air.
	await watchTo(video, 3_000, 3);
	expect(reports()).toHaveLength(1);
	const sent = heatTotal(reports()[0]);
	expect(sent).toBeGreaterThan(0);

	// A second of watching WHILE it is outstanding.
	clock += 1_000;
	video.currentTime = 4;
	video.dispatchEvent(new Event('timeupdate'));
	flushSync();

	if (!land) throw new Error('the report was never issued, so nothing was held open');
	(land as () => void)();
	await settle();

	// The file ends, which closes the pass and reports what is left.
	await playThrough(video);

	const closing = reports()[reports().length - 1];
	expect(
		heatTotal(closing),
		'the second of watching that landed while the first report was in the air was thrown away'
	).toBeCloseTo(1_000, -2);
	expect(heatTotal(closing)).toBeLessThan(sent);
});

it('names the sitting, and every piece of it carries the same name', async () => {
	/* The sitting id is what keeps two reports for one sitting from being two rows, so a file left
	   open long enough to earn a view reads as one sitting. A repeat
	   does NOT re-mint it: a pass is a playthrough and earns its own view, and a sitting is
	   somebody in front of this file. */
	const video = await watching();

	await watch(video, 3_000);
	await playThrough(video);
	await watch(video, 3_000);

	const names = reports().map((body) => body.sitting);
	expect(names[0]).toEqual(expect.any(String));
	expect(new Set(names).size).toBe(1);
});

it('says where the sitting is happening on every piece of it', async () => {
	/* Handed in by the frame, because the player is the same component in the panel and in the
	   corner. Every piece carries it, so the server keeps it whichever piece lands first. */
	const place = { screen: 'corner' } as const;
	const video = await watching({ place });

	await watch(video, 3_000);
	await playThrough(video);
	await watch(video, 3_000);

	expect(reports().length).toBeGreaterThan(1);
	for (const body of reports()) expect(body.screen).toBe('corner');
});

it('counts the jumps, and charges each one to the piece it happened in', async () => {
	/* The seek count is kept, not only detected: `spreadHeat` has to tell a jump from a stretch of
	   watching or the replay curve smears across everything the scrubber crossed. A jump is a
	   playhead that moved much further than the time that passed. */
	const video = await watching({ seconds: 10 });

	// Three seconds of real playing, which earns the view and sends the first piece.
	await watchTo(video, 3_000, 3);
	// Then a drag to the end: the playhead moves seven seconds in a quarter of one.
	await watchTo(video, 250, 9.5);
	await watchTo(video, 1_000, 9.6);
	// The end of the pass, which is what sends the piece the jump belongs to.
	await playThrough(video);

	const sent = reports();
	// The piece before the jump says nothing happened; the piece after it carries the one jump.
	// Charged per PIECE, like the time and the replay map beside it, so the server adds them up.
	expect(sent[0].seeks).toBe(0);
	expect(sent[1].seeks).toBe(1);
});

it('keeps each jump as where it was and where it went, and where the sitting began', async () => {
	const video = await watching({ seconds: 10 });

	await watchTo(video, 3_000, 3);
	await watchTo(video, 250, 9.5);
	await playThrough(video);

	const sent = reports();
	// Every piece carries where the sitting began; the server keeps the first piece's.
	expect(sent.every((body) => body.start_ms === 0)).toBe(true);
	expect(sent[0].seek_log).toEqual([]);
	expect(sent[1].seek_log).toEqual([{ from_ms: 3_000, to_ms: 9_500 }]);
});

it('counts every pass through the end under Repeat, each on the piece it closed', async () => {
	/* `ended` says that a pass happened; a clip on Repeat played through three times is three, and
	   only the element sees each one. */
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

	// The first pass's closing piece is lost; the second pass's carries both.
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
	/* The other half of the rule, and the reason it is a distance rather than `!played`: a paused
	   stretch moves the playhead by nothing over several seconds, which is the opposite of a seek.
	   Counting it would put a jump on every pause anybody ever made. */
	const video = await watching({ seconds: 10 });

	await watchTo(video, 1_000, 1);
	// Five seconds pass with the playhead where it was.
	await watchTo(video, 5_000, 1);
	await playThrough(video);

	expect(reports().every((body) => body.seeks === 0)).toBe(true);
});
