import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LiveFeed, liveWords, nextStep } from './live.svelte';
import { ApiError } from '$lib/api/client';
import {
	arrivals,
	assetState,
	downloadChanges,
	jobChanges,
	libraryChanges,
	mine,
	settingChanges
} from '$lib/library/changes.svelte';
import type { components } from '$lib/api/schema';

const answers = vi.hoisted(() => ({ marker: '5', fail: null as unknown }));

vi.mock('$lib/api/client', async (original) => {
	const real = (await original()) as Record<string, unknown>;
	return {
		...real,
		api: {
			get: async () => {
				if (answers.fail) throw answers.fail;
				return { about: [], marker: answers.marker, opinions: [], more_opinions: false };
			}
		}
	};
});

/* What a screen is told, given what the server said.
 *
 * The connection itself is not the interesting part. It is a WebSocket, and its shape is the job
 * feed's, which is proved where that lives. What is worth pinning down is the pair of decisions
 * this file adds: which message rings the bell every scoped screen is listening to, and what to do
 * about a connection that has gone away when the reason it went is not knowable from the close.
 */

type LiveState = components['schemas']['LiveState'];

function said(about: LiveState['about'], marker = '7', extra: Partial<LiveState> = {}): LiveState {
	return { about, marker, opinions: [], more_opinions: false, commands: [], ...extra };
}

let feed: LiveFeed;

beforeEach(() => {
	feed = new LiveFeed();
});

describe('what a message does', () => {
	it('rings the bell every scoped screen is already listening to', () => {
		const before = libraryChanges.generation;

		feed.apply(said(['library']));

		expect(libraryChanges.generation).toBe(before + 1);
	});

	it('says nothing to anybody when nothing moved', () => {
		/* An idle beat sends no message at all, so this is the shape of a message about something
		   this browser does not draw, which must not make every list on screen re-read. */
		const before = libraryChanges.generation;

		feed.apply(said([]));

		expect(libraryChanges.generation).toBe(before);
	});

	it('remembers where this account now stands', () => {
		feed.apply(said(['library'], '12'));

		expect(feed.marker).toBe('12');
	});

	it('remembers it even when there was nothing to be told', () => {
		/* The mark is what a reconnect compares against, so it has to keep up with the server on
		   every message, not only on the ones that changed a screen. Left behind, the next
		   reconnect would decide something had been missed and re-read for nothing. */
		feed.apply(said([], '12'));

		expect(feed.marker).toBe('12');
	});
});

describe('each subject reaching what it is for', () => {
	it('rings the bell the settings screens and the stores that act on a preference watch', () => {
		/* A setting is saved on one computer and read on another. Without this it would take effect
		   at the next page load, which reads as a switch that did nothing. */
		const before = settingChanges.generation;

		feed.apply(said(['settings']));

		expect(settingChanges.generation).toBe(before + 1);
	});

	it("rings the bell for a list that is this account's own", () => {
		const before = mine.generation;

		feed.apply(said(['mine']));

		expect(mine.generation).toBe(before + 1);
	});

	it('applies what this account thinks of a file WITHOUT making any screen re-read', () => {
		/* **The property that keeps a rating control from costing a page fetch per press.**
		   A star arrives as the row itself, applied by the screens already drawing it. Ringing the
		   library bell here would make every scoped wall on screen re-read, once per press. */
		const walls = libraryChanges.generation;
		const files = arrivals.generation;
		const seen = assetState.generation;

		feed.apply(
			said(['opinions'], '7', {
				opinions: [
					{ asset_id: 'a1', favorite: true, rating: 4, views: 2, pinned: false, o_count: 0 }
				]
			})
		);

		expect(assetState.generation).toBe(seen + 1);
		expect(assetState.last).toMatchObject({ asset_id: 'a1', rating: 4, views: 2 });
		expect(libraryChanges.generation).toBe(walls);
		expect(arrivals.generation).toBe(files);
	});

	it('asks the screens to re-read when more were written than a message carries', () => {
		/* A page is one read whatever it holds, so past a certain number re-reading is both cheaper
		   than carrying the rows and more correct than carrying some of them. */
		const files = arrivals.generation;

		feed.apply(said(['opinions'], '7', { opinions: [], more_opinions: true }));

		expect(arrivals.generation).toBe(files + 1);
	});

	it('ignores a subject it has never heard of rather than dying on it', () => {
		/* A shell on one computer may be a different version from the server on another. An unknown
		   name reaching a lookup and being called would take the whole connection down, silently. */
		const before = libraryChanges.generation;

		expect(() =>
			feed.apply(said(['library', 'something-later'] as LiveState['about']))
		).not.toThrow();

		expect(libraryChanges.generation).toBe(before + 1);
	});
});

describe('a connection that has gone away', () => {
	it('gives up when it was told plainly that it may not', () => {
		/* 1008 arrives intact, because by then there was a connection to carry it. Coming back
		   would be hammering the server forever on behalf of somebody who is never let in. */
		expect(nextStep(1008, true)).toBe('give-up');
		expect(nextStep(1008, false)).toBe('give-up');
	});

	it('comes back when it worked and then stopped', () => {
		/* A reload, a proxy timeout, a laptop lid. Nothing to ask anybody: it was allowed a moment
		   ago, so what changed was the network. */
		expect(nextStep(1006, true)).toBe('come-back');
		expect(nextStep(1001, true)).toBe('come-back');
	});

	it('comes back when it could not keep up', () => {
		/* Closed for falling behind, which is not a refusal. Coming back is cheap because the mark
		   decides whether anything actually needs re-reading. */
		expect(nextStep(1013, false)).toBe('come-back');
	});

	it('asks whether it is allowed at all when the close says nothing', () => {
		/* **The one that matters.** A handshake refused before it completes has no frame to carry a
		   reason, so the browser reports 1006, which is also exactly what a dropped network looks
		   like. Guessing either way is wrong in one of the two directions, so it asks. */
		expect(nextStep(1006, false)).toBe('ask-whether');
	});
});

/* Coming back after being away.
 *
 * The connection itself is the job feed's shape and is proved there. What is only here is what the
 * client HOLDS while it is away. And holding the wrong thing does not break anything visibly, it
 * leaves a screen showing what it showed an hour ago while every part reports success.
 */

/** A socket that connects to nothing, so the sequence can be driven by hand. */
class FakeSocket {
	static made: FakeSocket[] = [];
	onopen: (() => void) | null = null;
	onclose: ((event: { code: number }) => void) | null = null;
	onmessage: ((event: { data: string }) => void) | null = null;
	closed = false;

	constructor(readonly url: string) {
		FakeSocket.made.push(this);
	}

	close(): void {
		this.closed = true;
	}

	/** It became a connection, and then the network went. */
	dropAfterOpening(): void {
		this.onopen?.();
		this.onclose?.({ code: 1006 });
	}

	/** It never became one: refused, or asked for while there was no network. */
	failToOpen(): void {
		this.onclose?.({ code: 1006 });
	}
}

describe('what it holds while it is away', () => {
	beforeEach(() => {
		FakeSocket.made = [];
		answers.marker = '5';
		answers.fail = null;
		vi.stubGlobal('WebSocket', FakeSocket);
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.unstubAllGlobals();
	});

	async function started(): Promise<LiveFeed> {
		const feed = new LiveFeed();
		feed.start();
		await vi.waitFor(() => expect(FakeSocket.made.length).toBe(1));
		return feed;
	}

	it('starts from where the account stands, so a change between the page and the connection is caught', async () => {
		await started();

		expect(FakeSocket.made[0].url).toContain('since=5');
	});

	it('**keeps the mark it was last told, even when asking whether it is still allowed**', async () => {
		/* The one that matters, and the one that fails silently.
		 *
		 * A handshake that never completed cannot say why, so the client asks the plain read, and
		 * that read answers with where the account stands NOW. Taking that as the new mark adopts a
		 * position this screen was never brought up to: the next handshake then says "nothing has
		 * moved since", truthfully, about a mark nobody acted on, and the screen stays stale for
		 * good. Every part of that reports success. */
		const feed = await started();
		expect(feed.marker).toBe('5');

		// Something moved while this browser was away.
		answers.marker = '9';
		FakeSocket.made[0].failToOpen();
		await vi.waitFor(() => expect(feed.marker).toBe('5'));

		await vi.advanceTimersByTimeAsync(2500);
		await vi.waitFor(() => expect(FakeSocket.made.length).toBe(2));
		expect(FakeSocket.made[1].url).toContain('since=5');
	});

	it('keeps it through an ordinary drop as well', async () => {
		const feed = await started();
		answers.marker = '9';

		FakeSocket.made[0].dropAfterOpening();
		await vi.advanceTimersByTimeAsync(2500);

		await vi.waitFor(() => expect(FakeSocket.made.length).toBe(2));
		expect(FakeSocket.made[1].url).toContain('since=5');
	});

	it('moves the mark on only when the server actually says something', async () => {
		const feed = await started();

		FakeSocket.made[0].onopen?.();
		FakeSocket.made[0].onmessage?.({ data: JSON.stringify({ about: ['library'], marker: '9' }) });

		expect(feed.marker).toBe('9');
	});

	it('gives up quietly when it is told it may not', async () => {
		const feed = await started();

		FakeSocket.made[0].onclose?.({ code: 1008 });
		await vi.advanceTimersByTimeAsync(5000);

		expect(FakeSocket.made.length).toBe(1);
	});

	/* THE ENDLESS LOOP.
	 *
	 * The cap is on the socket and is refused before the handshake completes, so the browser is
	 * told 1006 and nothing else, and the plain read is how this client tells a refusal from a
	 * dropped connection. If that read answered 200 (it has no socket to be over the cap on), the
	 * client would be told "you are allowed" every time and come back every two seconds, for as
	 * long as the tab stayed open, on behalf of an account that was never going to be let in.
	 *
	 * The read says 429, and these two are what keep the loop from coming back.
	 */
	it('waits properly when the account already holds every connection it may', async () => {
		const feed = await started();
		answers.fail = new ApiError(429, 'too many live connections are open for this account');

		FakeSocket.made[0].failToOpen();

		// The ordinary reconnect would have made a second socket by here, and several more after it.
		await vi.advanceTimersByTimeAsync(5000);
		expect(FakeSocket.made.length, 'it did not come straight back').toBe(1);

		answers.fail = null;
		await vi.advanceTimersByTimeAsync(30_000);
		await vi.waitFor(() => expect(FakeSocket.made.length, 'and it did come back').toBe(2));
	});

	it('does not open a socket at all when the read has already said so', async () => {
		/* The earlier answer. A screen loading reads the plain endpoint BEFORE it opens anything, so
		   an account that is already over the cap can find out without making a handshake that
		   cannot succeed. */
		answers.fail = new ApiError(429, 'too many live connections are open for this account');

		const feed = new LiveFeed();
		feed.start();
		await vi.advanceTimersByTimeAsync(5000);

		expect(FakeSocket.made.length, 'nothing was opened').toBe(0);

		answers.fail = null;
		await vi.advanceTimersByTimeAsync(30_000);
		await vi.waitFor(() => expect(FakeSocket.made.length, 'until the cap cleared').toBe(1));
	});
});

/* Saying that this browser is not being told anything.
 *
 * Every screen in Sift reads its list once and is then told when it moved, so with no connection
 * nothing moves. And a wall showing what it showed eight minutes ago is indistinguishable from
 * a library in which nothing has happened: an account at the connection cap would watch tiles sit
 * on "Importing..." while the files behind them finished, with every part of it reporting
 * success. Two things answer it: a word the header can draw, and a slow re-read so the wall is
 * stale by thirty seconds rather than for ever.
 */
describe('what the screen is told about the connection itself', () => {
	it('says nothing at all while the first connection is being made', () => {
		/* A line during the second a page takes to connect would be on every load, which is how a
		   warning stops being read. */
		expect(liveWords('opening')).toBe('');
		expect(liveWords('live')).toBe('');
	});

	it('names the cap in words that say what to do about it', () => {
		/* The refusal is "this account already holds every connection it may", and what clears it is
		   closing a window somewhere else, so the sentence is about windows, not about a server. */
		expect(liveWords('full')).toBe('Live updates are paused: too many windows are open');
	});

	it('says it is coming back, and says when it is not', () => {
		expect(liveWords('reconnecting')).toBe('Reconnecting\u2026');
		expect(liveWords('stopped')).toBe('Live updates are off');
	});
});

describe('where this browser stands', () => {
	beforeEach(() => {
		FakeSocket.made = [];
		answers.marker = '5';
		answers.fail = null;
		vi.stubGlobal('WebSocket', FakeSocket);
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.unstubAllGlobals();
	});

	async function started(): Promise<LiveFeed> {
		const feed = new LiveFeed();
		feed.start();
		await vi.waitFor(() => expect(FakeSocket.made.length).toBe(1));
		return feed;
	}

	it('is live once the connection opens, and says nothing while it is', async () => {
		const feed = await started();

		FakeSocket.made[0].onopen?.();

		expect(feed.standing).toBe('live');
		expect(liveWords(feed.standing)).toBe('');
	});

	it('**says the account is at the cap rather than going quiet**', async () => {
		/* The socket is refused before the handshake completes, so the browser is told nothing;
		   the client knows and the screen has to say so. */
		answers.fail = new ApiError(429, 'too many live connections are open for this account');

		const feed = new LiveFeed();
		feed.start();
		await vi.advanceTimersByTimeAsync(100);

		expect(feed.standing).toBe('full');
		expect(liveWords(feed.standing)).toBe('Live updates are paused: too many windows are open');
	});

	it('says it is coming back after an ordinary drop', async () => {
		const feed = await started();

		FakeSocket.made[0].dropAfterOpening();

		expect(feed.standing).toBe('reconnecting');
	});

	it('says live updates are off when it was told plainly that it may not', async () => {
		/* 1008 is the one close that arrives intact, and nothing comes back from it. A screen that
		   said nothing here would never move again and never say why. */
		const feed = await started();

		FakeSocket.made[0].onclose?.({ code: 1008 });

		expect(feed.standing).toBe('stopped');
		expect(liveWords(feed.standing)).toBe('Live updates are off');
	});
});

describe('the wall while there is no connection', () => {
	beforeEach(() => {
		FakeSocket.made = [];
		answers.marker = '5';
		answers.fail = null;
		vi.stubGlobal('WebSocket', FakeSocket);
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.unstubAllGlobals();
	});

	it('**asks every screen to re-read on a slow timer, so nothing freezes**', async () => {
		/* The fallback. It is deliberately all six subjects: what was missed while there was no
		   connection is unknown, so choosing which bells to ring would be guessing what the server
		   would have said. */
		answers.fail = new ApiError(429, 'too many live connections are open for this account');
		const feed = new LiveFeed();
		const before = {
			library: libraryChanges.generation,
			files: arrivals.generation,
			jobs: jobChanges.generation,
			downloads: downloadChanges.generation,
			settings: settingChanges.generation,
			mine: mine.generation
		};

		feed.start();
		await vi.advanceTimersByTimeAsync(31_000);

		expect(libraryChanges.generation).toBeGreaterThan(before.library);
		expect(arrivals.generation).toBeGreaterThan(before.files);
		expect(jobChanges.generation).toBeGreaterThan(before.jobs);
		expect(downloadChanges.generation).toBeGreaterThan(before.downloads);
		expect(settingChanges.generation).toBeGreaterThan(before.settings);
		expect(mine.generation).toBeGreaterThan(before.mine);
		feed.stop();
	});

	it('does nothing at all while the connection is open', async () => {
		/* The whole reason the connection exists is that it spares a page read per screen per
		   second. A fallback that kept running underneath a working connection would put that back,
		   quietly, on every install. */
		const feed = new LiveFeed();
		feed.start();
		await vi.waitFor(() => expect(FakeSocket.made.length).toBe(1));
		FakeSocket.made[0].onopen?.();
		const before = arrivals.generation;

		await vi.advanceTimersByTimeAsync(120_000);

		expect(arrivals.generation).toBe(before);
		feed.stop();
	});

	it('stops when the feed does, so signing out leaves no timer behind', async () => {
		answers.fail = new ApiError(429, 'too many live connections are open for this account');
		const feed = new LiveFeed();
		feed.start();
		await vi.advanceTimersByTimeAsync(31_000);
		feed.stop();
		const before = arrivals.generation;

		await vi.advanceTimersByTimeAsync(120_000);

		expect(arrivals.generation).toBe(before);
	});
});
