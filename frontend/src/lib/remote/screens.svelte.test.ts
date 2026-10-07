/*
 * The phone's list of screens, and whether a press landed.
 *
 * What would be silent if it broke: a position read off the wall clock (which can step backwards),
 * a screen that went quiet staying pressable until somebody reloads,
 * the picked screen hopping to whichever desk spoke last, a phone back from the background showing
 * a stale list, a refusal swallowed, and a press the screen never heard looking like one on its way.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '$lib/api/client';

import { RemoteList } from './screens.svelte';

// The store's own two numbers, pinned here rather than exported: five seconds for a screen to
// report a press back, and the one line it says when none came.
const ANSWER_MS = 5_000;
const NO_ANSWER = "The screen didn't answer. It may be asleep or closed.";
import type { RemoteScreens, ScreenOut } from './wire';

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() }
}));

const mocked = vi.mocked(api);

let clock = 0;

/** What a screen says about its drawer and lists when it has none. */
const NOTHING_MORE = {
	repeat: null,
	shuffle: null,
	loop_marks: 0,
	qualities: [],
	quality: null,
	favorite: null,
	count: null,
	timer: null,
	every_cell: false,
	cell_held: null,
	cell_muted: null,
	layouts: [],
	layout: null,
	presets: [],
	cell_files: []
} satisfies Partial<ScreenOut>;

function aScreen(extra: Partial<ScreenOut> = {}): ScreenOut {
	return {
		screen: 'screen-a',
		label: 'The Sift app on Windows',
		surface: 'player',
		app: true,
		playing: true,
		position: 10,
		length: 100,
		file: null,
		hidden: false,
		volume: 70,
		muted: false,
		supports: ['player.playPause'],
		acted_on: null,
		cells: 0,
		focused: null,
		...NOTHING_MORE,
		heard_seconds_ago: 0,
		controlled_by: [],
		...extra
	};
}

function answering(...screens: ScreenOut[]): void {
	mocked.get.mockResolvedValue({
		screens,
		listed_for_seconds: 30,
		not_offering: 0
	} satisfies RemoteScreens);
}

function aList(): RemoteList {
	return new RemoteList(() => clock);
}

beforeEach(() => {
	vi.clearAllMocks();
	clock = 1_000;
});

describe('the list', () => {
	it("carries a playing screen's position on by the page's own clock, and holds a paused one", async () => {
		answering(aScreen(), aScreen({ screen: 'screen-b', playing: false, position: 40 }));
		const list = aList();
		await list.load();

		clock += 7_500;
		list.tick();

		expect(list.positionOf(list.screens[0])).toBe(17.5);
		expect(list.positionOf(list.screens[1])).toBe(40);
	});

	it('never carries a position past the length', async () => {
		answering(aScreen({ position: 98 }));
		const list = aList();
		await list.load();

		clock += 10_000;
		list.tick();

		expect(list.positionOf(list.screens[0])).toBe(100);
	});

	it('lets a screen that went quiet go when the server would, without waiting for a re-read', async () => {
		answering(aScreen({ heard_seconds_ago: 25 }), aScreen({ screen: 'screen-b' }));
		const list = aList();
		await list.load();
		expect(list.live.map((one) => one.screen)).toEqual(['screen-a', 'screen-b']);

		clock += 6_000;
		list.tick();

		expect(list.live.map((one) => one.screen)).toEqual(['screen-b']);
		expect(list.current?.screen).toBe('screen-b');
	});

	it('picks the screen heard from last, and keeps it while the order changes under it', async () => {
		answering(aScreen(), aScreen({ screen: 'screen-b' }));
		const list = aList();
		await list.load();
		expect(list.current?.screen).toBe('screen-a');

		answering(aScreen({ screen: 'screen-b' }), aScreen());
		await list.load();

		expect(list.current?.screen).toBe('screen-a');
	});

	it('picks again, the same way, only once the picked screen has gone', async () => {
		answering(aScreen(), aScreen({ screen: 'screen-b' }));
		const list = aList();
		await list.load();
		list.pick('screen-b');

		answering(aScreen({ screen: 'screen-c' }), aScreen());
		await list.load();

		expect(list.current?.screen).toBe('screen-c');
	});

	it('re-reads when the page comes back from the background', async () => {
		answering(aScreen());
		const list = aList();
		const stop = list.watch();
		await vi.waitFor(() => expect(mocked.get).toHaveBeenCalledTimes(1));

		Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
		document.dispatchEvent(new Event('visibilitychange'));

		await vi.waitFor(() => expect(mocked.get).toHaveBeenCalledTimes(2));
		stop();
		document.dispatchEvent(new Event('visibilitychange'));
		expect(mocked.get).toHaveBeenCalledTimes(2);
	});
});

describe('a press', () => {
	it("says the server's own words when a press is refused", async () => {
		mocked.post.mockRejectedValue(
			new ApiError(409, "That's already done.", "That screen can't do that.")
		);
		const list = aList();

		expect(await list.send('screen-a', 'player.next')).toBeNull();

		expect(list.problem).toBe("That screen can't do that.");
	});

	it('re-reads the list when the screen is gone, so it leaves the page immediately', async () => {
		answering();
		mocked.post.mockRejectedValue(
			new ApiError(404, 'Not found.', "Sift couldn't find that screen.")
		);
		const list = aList();

		await list.send('screen-a', 'player.next');

		await vi.waitFor(() => expect(mocked.get).toHaveBeenCalledTimes(1));
		expect(list.problem).toBe("Sift couldn't find that screen.");
	});

	it('says the screen did not answer when its id never comes back', async () => {
		answering(aScreen());
		mocked.post.mockResolvedValue({ id: 'c-1' });
		const list = aList();
		await list.load();
		await list.send('screen-a', 'player.playPause', 0);

		clock += ANSWER_MS - 1;
		list.tick();
		expect(list.problem).toBeNull();

		clock += 1;
		list.tick();
		expect(list.problem).toBe(NO_ANSWER);
	});

	it('takes the line away once that screen speaks again, since it is not asleep after all', async () => {
		/* Otherwise the line stands under a card that has just reported a new
		   position, until the next press. */
		answering(aScreen());
		mocked.post.mockResolvedValue({ id: 'c-1' });
		const list = aList();
		await list.load();
		await list.send('screen-a', 'player.playPause', 0);
		clock += ANSWER_MS;
		list.tick();
		expect(list.problem).toBe(NO_ANSWER);

		// A read that only repeats what the screen said before the press gave up changes nothing.
		answering(aScreen({ heard_seconds_ago: (ANSWER_MS + 1_000) / 1000 }));
		await list.load();
		expect(list.problem).toBe(NO_ANSWER);

		clock += 2_000;
		answering(aScreen({ heard_seconds_ago: 1 }));
		await list.load();
		expect(list.problem).toBeNull();
	});

	it('takes the line away when another screen is picked, and keeps it for the one that went quiet', async () => {
		answering(aScreen(), aScreen({ screen: 'screen-b', label: 'Chrome on Windows' }));
		mocked.post.mockResolvedValue({ id: 'c-1' });
		const list = aList();
		await list.load();
		await list.send('screen-a', 'player.playPause', 0);
		clock += ANSWER_MS;
		list.tick();

		list.pick('screen-a');
		expect(list.problem).toBe(NO_ANSWER);
		list.pick('screen-b');
		expect(list.problem).toBeNull();
	});

	it('is answered when the screen reports the id back', async () => {
		answering(aScreen());
		mocked.post.mockResolvedValue({ id: 'c-1' });
		const list = aList();
		await list.load();
		await list.send('screen-a', 'player.playPause', 0);

		answering(aScreen({ acted_on: 'c-1', playing: false }));
		await list.load();
		clock += ANSWER_MS * 2;
		list.tick();

		expect(list.problem).toBeNull();
	});
});

describe('the names', () => {
	it("names a file through the phone's own door, once, and never asks for a file it was not given", async () => {
		mocked.get.mockImplementation(async (path: string) =>
			path === '/remote/screens'
				? {
						screens: [aScreen({ file: 'f1' }), aScreen({ screen: 'screen-b', hidden: true })],
						listed_for_seconds: 30
					}
				: { filename: 'beach.mp4' }
		);
		const list = aList();
		await list.load();
		await list.load();

		await vi.waitFor(() => expect(list.names).toEqual({ f1: 'beach.mp4' }));
		expect(mocked.get.mock.calls.map(([path]) => path)).toEqual([
			'/remote/screens',
			'/assets/f1',
			'/remote/screens'
		]);
	});
});

describe('saying which screen this phone drives', () => {
	function holds(): string[] {
		return mocked.put.mock.calls.map(([path]) => `PUT ${path}`);
	}
	function lets(): string[] {
		return mocked.del.mock.calls.map(([path]) => `DELETE ${path}`);
	}

	it('holds the screen on the card while watched, moves with a pick, and lets go on leaving', async () => {
		mocked.put.mockResolvedValue(undefined);
		mocked.del.mockResolvedValue(undefined);
		answering(aScreen(), aScreen({ screen: 'screen-b' }));
		const list = new RemoteList(
			() => clock,
			() => 'Chrome on Android'
		);
		const stop = list.watch();
		await vi.waitFor(() => expect(list.read).toBe(true));
		const phone = list.controller;

		expect(holds()).toEqual([`PUT /remote/screens/screen-a/controllers/${phone}`]);
		expect(mocked.put.mock.calls[0][1]).toEqual({ body: { label: 'Chrome on Android' } });

		/* A re-read inside the renewal time says nothing again; one past it renews. */
		await list.load();
		expect(holds()).toHaveLength(1);
		clock += 10_000;
		await list.load();
		expect(holds()).toHaveLength(2);

		list.pick('screen-b');
		expect(lets()).toEqual([`DELETE /remote/screens/screen-a/controllers/${phone}`]);
		expect(holds().at(-1)).toBe(`PUT /remote/screens/screen-b/controllers/${phone}`);

		stop();
		expect(lets().at(-1)).toBe(`DELETE /remote/screens/screen-b/controllers/${phone}`);
	});

	it('holds nothing when only read, never watched', async () => {
		answering(aScreen());
		await aList().load();
		expect(mocked.put).not.toHaveBeenCalled();
	});
});
