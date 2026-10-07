/* Moving between assets, from whatever was on screen when one was opened.
 *
 * The module holds the list behind an open asset, and there are two different questions asked of
 * it. They look like one question and they are not, which is the whole reason this file exists.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The navigation half of the module talks to SvelteKit's history, which is not a thing a unit test
// has. It is stood in for rather than skipped: the sequence tests below only need it to exist, and
// the panel tests at the bottom read what was written to it.
const pushState = vi.fn();
const replaceState = vi.fn();
/* A press asks its plan beside the record (`planBeside`); nothing here is about the plan. */
vi.mock('$lib/player/playback', () => ({ planFor: async () => ({}) }));
vi.mock('$app/navigation', () => ({
	pushState: (...args: unknown[]) => pushState(...args),
	replaceState: (...args: unknown[]) => replaceState(...args)
}));

/* The viewer reads `page.state` to carry `direct` from one file to the next. A stand-in that can
   answer both of the things that flag can be. One that could only ever answer one would make the
   carrying rule untestable in the direction that matters. */
const pageState: { direct?: boolean } = {};
/* The screen behind the panel, which is what a sitting says it was opened from. Shallow routing
   leaves both of these on that screen while the panel is up, and the stand-in lets a test put them
   anywhere. */
const behind: { route: string | null; url: URL; params: Record<string, string> } = {
	route: '/browse',
	url: new URL('http://sift.test/browse'),
	params: {}
};
vi.mock('$app/state', () => ({
	page: {
		get state() {
			return pageState;
		},
		get route() {
			return { id: behind.route };
		},
		get url() {
			return behind.url;
		},
		get params() {
			return behind.params;
		}
	}
}));

import {
	canStepBack,
	canStepForward,
	enterAsset,
	forgetAsset,
	neighboursOf,
	nextPlayableAfter,
	NAMES_ITS_THING,
	OPENED_FROM,
	OPENS_NO_FILE,
	openAsset,
	panelPlace,
	playOn,
	playOnAfter,
	runGoesOn,
	showAsset,
	showStranger,
	stepAwayFrom,
	toggleShuffle,
	type Continues,
	stepBack,
	stepForward,
	type RunOrder
} from './asset-view';
import { run } from './run.svelte';
import { dwell } from './dwell.svelte';

const CLIP = (id: string) => ({ id, runs: true });
const PHOTO = (id: string) => ({ id, runs: false });

describe('stepping through what was on screen', () => {
	beforeEach(() => {
		openAsset('b', [CLIP('a'), CLIP('b'), PHOTO('c'), CLIP('d')]);
	});

	it('gives the immediate neighbours, whatever they are', () => {
		// The buttons walk everything. Somebody pressing Next means the next one: if they wanted
		// the photographs skipped they would not be pressing a button per item.
		expect(neighboursOf('b')).toEqual({ previous: 'a', next: 'c' });
	});

	it('ends at the ends', () => {
		expect(neighboursOf('a').previous).toBeNull();
		expect(neighboursOf('d').next).toBeNull();
	});

	it('knows nothing about an asset that was not in the list', () => {
		// Opened from a bare link rather than from a grid. There is no list behind it and therefore
		// nowhere to go, which has to be reported as nowhere rather than as the first item.
		expect(neighboursOf('zzz')).toEqual({ previous: null, next: null });
		expect(nextPlayableAfter('zzz')).toBeNull();
	});
});

describe('playing through, which is a different question', () => {
	it('steps over a still to the next thing that plays', () => {
		/* A photograph has no end to reach (there is no video element to fire `ended`), so a run
		 * that advanced onto one would stop there and wait for somebody to come back to the machine:
		 * "play through" doing nothing. */
		openAsset('b', [CLIP('a'), CLIP('b'), PHOTO('c'), CLIP('d')]);

		expect(nextPlayableAfter('b')).toBe('d');
	});

	it('steps over a whole run of them', () => {
		openAsset('a', [CLIP('a'), PHOTO('b'), PHOTO('c'), PHOTO('d'), CLIP('e')]);

		expect(nextPlayableAfter('a')).toBe('e');
	});

	it('stops when everything left is a still', () => {
		// Null and the end of the list mean the same thing here (there is nothing further to play),
		// and both correctly stop rather than looping or landing on something silent.
		openAsset('a', [CLIP('a'), PHOTO('b'), PHOTO('c')]);

		expect(nextPlayableAfter('a')).toBeNull();
	});

	it('agrees with the buttons when there is nothing in the way', () => {
		openAsset('a', [CLIP('a'), CLIP('b')]);

		expect(nextPlayableAfter('a')).toBe('b');
		expect(neighboursOf('a').next).toBe('b');
	});

	it('never goes backwards to find something playable', () => {
		// Only forwards. A run reaching the end is over; turning round and playing the earlier clips
		// again is a loop nobody asked for, and "repeat" is the setting that does that on purpose.
		openAsset('c', [CLIP('a'), CLIP('b'), PHOTO('c')]);

		expect(nextPlayableAfter('c')).toBeNull();
	});
});

/*
 * Landing on a file's address COLD, which is the case with no grid behind it.
 *
 * `/asset/{id}` is always a panel. What differs is only what is underneath: opened from a tile
 * there is a grid to go back to, and opened from a link there is nothing. So closing has to go to
 * the library rather than stepping out of Sift entirely. The flag is what tells the two apart, and
 * stepping to the next clip does not put a screen behind the panel, so it has to survive the step.
 */
describe('an address landed on cold', () => {
	beforeEach(() => {
		pushState.mockClear();
		replaceState.mockClear();
		delete pageState.direct;
	});

	it('opens the viewer with nothing behind it, and says so', () => {
		enterAsset('m1');

		expect(replaceState).toHaveBeenCalledWith('', { asset: 'm1', at: undefined, direct: true });
	});

	it('keeps a moment it was given, so a face opens at the second it was found at', () => {
		enterAsset('m1', 42_000);

		expect(replaceState).toHaveBeenCalledWith('', { asset: 'm1', at: 42_000, direct: true });
	});

	it('replaces rather than pushes, so one Back leaves the way it would from any first page', () => {
		enterAsset('m1');

		expect(pushState).not.toHaveBeenCalled();
	});

	it('is NOT cold when the address was followed from a screen inside the app', () => {
		/* A route that runs for two arrivals: a cold load, where there is nothing behind the
		   panel and closing has to leave for Browse, and a plain link followed from a screen,
		   where that screen is one step back. Marking every entry direct would close a file
		   opened from a Disagreements card onto Browse. The caller answers it, because
		   SvelteKit's own navigation is the only thing that knows: `from === null` is the first
		   page of a session and nothing else. */
		enterAsset('m1', undefined, undefined, { cold: false });

		expect(replaceState).toHaveBeenCalledWith('', {
			asset: 'm1',
			at: undefined,
			until: undefined,
			direct: false
		});
	});

	it('carries that forward when somebody steps to the next file', () => {
		pageState.direct = true;
		showAsset('m2');

		expect(replaceState).toHaveBeenCalledWith('/asset/m2', { asset: 'm2', direct: true });
	});

	it('and does not invent it for a file opened from a grid', () => {
		// The other value, because a flag that is always true is a flag that says nothing.
		showAsset('m2');

		expect(replaceState).toHaveBeenCalledWith('/asset/m2', { asset: 'm2', direct: undefined });
	});
});

/*
 * A RUN THAT REACHES PAST THE PAGE IT WAS OPENED FROM.
 *
 * The wall is paged: it draws sixty-five files out of eight thousand, and the sequence handed to
 * the panel is that page. "Play through" must not stop at the bottom of it. These are the two
 * halves that make it go end to end: whether it goes on at all, which has to be answered without
 * a request, and where it goes, which may be a page away.
 */

/** A list the run can read past the end of, standing in for the wall's own reader. */
function pagedList(pages: Record<number, ReturnType<typeof CLIP>[]>, total: number) {
	const asked: number[] = [];
	return {
		asked,
		reader: {
			from: 0,
			total,
			shuffles: false,
			async fetch(offset: number) {
				asked.push(offset);
				return pages[offset] ?? [];
			}
		}
	};
}

describe('a run that reaches past the loaded page', () => {
	it('walks into the next block rather than stopping at the end of the page', async () => {
		const { reader, asked } = pagedList({ 2: [CLIP('c'), CLIP('d')] }, 4);
		openAsset('b', [CLIP('a'), CLIP('b')], undefined, undefined, reader);

		// Nothing further down what is held: the end of the page, not the end of the list.
		expect(nextPlayableAfter('b')).toBeNull();
		expect(await playOnAfter('b')).toBe('c');
		expect(asked).toEqual([2]);
	});

	it('keeps asking while the blocks that come back hold nothing that plays', async () => {
		// A run of photographs is an ordinary library. Giving up on the first such block would stop
		// the run in the middle of the list.
		const { reader } = pagedList({ 1: [PHOTO('b'), PHOTO('c')], 3: [PHOTO('d'), CLIP('e')] }, 5);
		openAsset('a', [CLIP('a')], undefined, undefined, reader);

		expect(await playOnAfter('a')).toBe('e');
	});

	it('wraps to the beginning at the TRUE end of the list, not at the end of a page', async () => {
		const { reader } = pagedList({ 2: [CLIP('c')] }, 3);
		openAsset('b', [CLIP('a'), CLIP('b')], undefined, undefined, reader);

		expect(await playOnAfter('b')).toBe('c');
		// Now genuinely at the end. Play through means play through, so it starts again.
		expect(await playOnAfter('c')).toBe('a');
	});

	it('does not wrap onto the file that just finished while something else plays', async () => {
		openAsset('b', [PHOTO('a'), CLIP('b'), CLIP('c')]);
		// From the last one, the wrap skips the still and skips itself.
		expect(await playOnAfter('c')).toBe('b');
	});

	it('repeats the only thing that plays rather than stopping dead', async () => {
		openAsset('a', [CLIP('a'), PHOTO('b')]);
		expect(await playOnAfter('a')).toBe('a');
	});

	it('stops when nothing in the whole list plays', async () => {
		openAsset('a', [PHOTO('a'), PHOTO('b')]);
		expect(await playOnAfter('a')).toBeNull();
		expect(runGoesOn('a')).toBe(false);
	});

	it('goes on through photographs when the account asked for them to be held', () => {
		/*
		 * `runs: false` IS A FACT ABOUT THE FILE, NOT AN ANSWER ABOUT THE RUN.
		 *
		 * A still has no end to reach, so it never carries `runs`. Read as "there is nowhere to go",
		 * that would tell a run of photographs to stop on an install that had asked for photographs
		 * to be held. The setting is called "Include photos on a playthrough"; without this it would
		 * include them in everything except whether the run started.
		 */
		openAsset('a', [PHOTO('a'), PHOTO('b')]);

		expect(runGoesOn('a', { pictures: true })).toBe(true);
		// And one photograph on its own is still nowhere to go, whatever the preference says.
		openAsset('a', [PHOTO('a')]);
		expect(runGoesOn('a', { pictures: true })).toBe(false);
	});

	it('says a run goes on while there is more of the list, before any request', () => {
		const { reader, asked } = pagedList({}, 900);
		/* THE PAGE HOLDS NOTHING ELSE THAT PLAYS, and that is the whole case.
		 *
		 * With a second clip on the page the answer is true either way, so the test would pass
		 * whether or not the total is consulted. The bottom of a page, with more of the list
		 * underneath, is the state the fault actually occurs in.
		 */
		openAsset('b', [PHOTO('a'), CLIP('b')], undefined, undefined, reader);

		// It decides whether the player is handed an advance handler at all, so it has to answer
		// from the total rather than from the page, and without asking.
		expect(runGoesOn('b')).toBe(true);
		expect(asked).toEqual([]);
	});

	it('says a run stops at the end of a list it has all of', () => {
		// The other side, so the answer above cannot become a bare `true`.
		openAsset('b', [PHOTO('a'), CLIP('b')], undefined, undefined, {
			from: 0,
			total: 2,
			shuffles: false,
			fetch: async () => []
		});
		expect(runGoesOn('b')).toBe(false);
	});

	it('answers from what is held for a list that cannot be extended', async () => {
		openAsset('a', [CLIP('a'), CLIP('b')]);
		expect(await playOnAfter('a')).toBe('b');
		expect(runGoesOn('a')).toBe(true);
	});

	it('does not hand back a file it is already holding', async () => {
		// An import under a run re-orders the wall, so a block can carry a file already walked.
		// Appending it would put the run back where it was.
		const { reader } = pagedList({ 2: [CLIP('a'), CLIP('c')] }, 3);
		openAsset('b', [CLIP('a'), CLIP('b')], undefined, undefined, reader);

		expect(await playOnAfter('b')).toBe('c');
	});

	it('is not stopped by a request that fails', async () => {
		const reader = {
			from: 0,
			total: 9,
			shuffles: false,
			fetch: async () => {
				throw new Error('offline');
			}
		};
		openAsset('b', [CLIP('a'), CLIP('b')], undefined, undefined, reader);

		// It wraps rather than throwing: a run is not worth an error message.
		expect(await playOnAfter('b')).toBe('a');
	});
});

/*
 * SHUFFLE IS ONE FIXED SHUFFLED ORDER, walked forwards and backwards.
 *
 * Not a fresh draw per press: that makes Back go to the file beside this one in the LIST, lets
 * files come round again while others never play, and leaves the corner player out of it. These pin
 * the playlist.
 */
describe('Shuffle', () => {
	const FIVE = () => ['a', 'b', 'c', 'd', 'e'].map(CLIP);

	beforeEach(() => {
		run.shuffle = true;
		run.reset();
	});

	afterEach(() => {
		run.shuffle = false;
		run.reset();
		vi.restoreAllMocks();
	});

	/** Next, pressed `times` times from `from`, and every file it landed on. */
	async function nextTimes(from: string, times: number): Promise<string[]> {
		const landed: string[] = [];
		let at = from;
		for (let press = 0; press < times; press += 1) {
			const to = await stepForward(at);
			expect(to, `Next had nowhere to go on press ${press + 1}`).not.toBeNull();
			at = to as string;
			landed.push(at);
		}
		return landed;
	}

	it('plays nothing twice until everything has played', async () => {
		openAsset('a', FIVE());

		const landed = await nextTimes('a', 4);

		// Four presses, four DIFFERENT files, and never the one it started on, which a fresh draw
		// per press could not promise (it could repeat one file and never reach another).
		expect(new Set(landed).size).toBe(4);
		expect([...landed].sort()).toEqual(['b', 'c', 'd', 'e']);
		// Everything has played, so a new pass begins from where the walk started.
		expect(await stepForward(landed[3])).toBe('a');
	});

	it('walks the shuffled list once under Stop at the end, and stops where it runs out', async () => {
		/* Stop at the end under Shuffle is the end of the SHUFFLED LIST, not of the file: the run
		 * moves on through every file once and then has nowhere to go, where Play through goes
		 * round again from the file it began on. */
		dwell.repeats('once');
		try {
			openAsset('a', FIVE());
			expect(run.movesOnAfter('once')).toBe(true);
			const landed: string[] = [];
			let at = 'a';
			for (let step = 0; step < 4; step += 1) {
				const to = await playOn(at, { wraps: !dwell.stopsAtTheEnd });
				expect(to, `the run stopped early, on step ${step + 1}`).not.toBeNull();
				at = to as string;
				landed.push(at);
			}
			expect([...landed].sort()).toEqual(['b', 'c', 'd', 'e']);
			expect(await playOn(at, { wraps: !dwell.stopsAtTheEnd })).toBeNull();

			dwell.repeats('loop_all');
			expect(await playOn(at, { wraps: !dwell.stopsAtTheEnd })).toBe('a');
		} finally {
			dwell.repeats(undefined);
		}
	});

	it('goes Back to the file just watched, not to its neighbour in the list', async () => {
		openAsset('a', FIVE());
		const [first, second, third] = await nextTimes('a', 3);

		expect(stepBack(third)).toBe(second);
		expect(stepBack(second)).toBe(first);
		// And Next from there walks the same order again rather than drawing a new file.
		expect(await stepForward(first)).toBe(second);
	});

	it('goes Back from the first shuffled file to the file it started on', async () => {
		openAsset('c', FIVE());
		const [first] = await nextTimes('c', 1);

		expect(canStepBack(first)).toBe(true);
		expect(stepBack(first)).toBe('c');
		// The start of the walk: there is nothing before it, and the bar draws no Back.
		expect(canStepBack('c')).toBe(false);
		expect(stepBack('c')).toBeNull();
	});

	it('offers Back whenever the walk can step back, and only then', async () => {
		openAsset('a', FIVE());
		// Shuffle on and no step taken: the file open IS the start, and there is nothing behind it.
		expect(canStepBack('a')).toBe(false);
		expect(canStepForward('a')).toBe(true);

		const [first] = await nextTimes('a', 1);
		expect(canStepBack(first)).toBe(true);
	});

	it('shuffles what it holds when the list cannot be read any further', async () => {
		// A face pile, a link: no reader at all, so what was handed in is the whole list.
		openAsset('b', [CLIP('a'), CLIP('b'), CLIP('c')]);

		const landed = await nextTimes('b', 2);

		expect([...landed].sort()).toEqual(['a', 'c']);
		expect(await stepForward(landed[1])).toBe('b');
	});

	it("walks past what is held in the server's seeded order, one seed for the whole run", async () => {
		/* A hundred and thirty files, of which the page holds three. The stand-in server answers the
		   seeded arrangement as a fixed permutation (reversed), so what is asserted is that the walk
		   reads it block by block under ONE seed and reaches every file exactly once. */
		const all = Array.from({ length: 130 }, (_unused, at) => CLIP(`f${at}`));
		const arranged = [...all].reverse();
		const asked: { offset: number; seed: number | undefined }[] = [];
		const reader = {
			from: 0,
			total: all.length,
			shuffles: true,
			async fetch(offset: number, limit: number, order?: RunOrder) {
				asked.push({ offset, seed: order?.seed });
				return arranged.slice(offset, offset + limit);
			}
		};
		openAsset('f1', all.slice(0, 3), undefined, undefined, reader);

		const landed = await nextTimes('f1', 129);

		expect(new Set(landed).size).toBe(129);
		expect(landed).not.toContain('f1');
		expect(landed.slice(0, 2)).toEqual(['f129', 'f128']);
		// Every block asked for in the shuffled order, and every one under the same seed.
		expect(asked.map((one) => one.offset)).toEqual([0, 60, 120]);
		expect(new Set(asked.map((one) => one.seed)).size).toBe(1);
		expect(asked[0].seed).toEqual(expect.any(Number));
		// Everything has played: a new pass, from the file the walk started on.
		expect(await stepForward(landed[128])).toBe('f1');
	});

	it('reads a list its server cannot shuffle whole, and shuffles that', async () => {
		// The wall of Loops: more of it than is held, and no seeded order to ask for.
		const { reader, asked } = pagedList(
			{ 0: [CLIP('a'), CLIP('b')], 2: [CLIP('c'), CLIP('d')] },
			4
		);
		openAsset('a', [CLIP('a'), CLIP('b')], undefined, undefined, reader);

		const landed = await nextTimes('a', 3);

		expect([...landed].sort()).toEqual(['b', 'c', 'd']);
		expect(asked).toEqual([0, 2]);
	});

	it('plays through on the same order Next walks, stepping over stills', async () => {
		openAsset('a', [CLIP('a'), PHOTO('p'), CLIP('b'), PHOTO('q'), CLIP('c')]);

		const one = await playOn('a');
		const two = await playOn(one as string);

		// A run moving on by itself lands only on what plays, and never twice.
		expect(new Set([one, two])).toEqual(new Set(['b', 'c']));
		// Back from where play-through went is where it came from: one walk, not two.
		expect(stepBack(two as string)).toBe(one);
		expect(stepBack(one as string)).toBe('a');
	});

	it('does not land Back on a still the run stepped over', async () => {
		// Every draw zero, so the order is known: the shared shuffle rotates a-p-q-b to p-q-b-a, and
		// with the start file taken out the walk is p, q, b: the clip AFTER both stills.
		vi.spyOn(Math, 'random').mockReturnValue(0);
		openAsset('a', [CLIP('a'), PHOTO('p'), PHOTO('q'), CLIP('b')]);

		expect(await playOn('a')).toBe('b');
		// Back returns to what was WATCHED, which was the start, not to the position before this
		// one, which is a photograph nobody was shown.
		expect(stepBack('b')).toBe('a');
	});

	it('takes a file reached by Randomize as a detour, and Back returns from it', async () => {
		openAsset('a', FIVE());
		const [first, second] = await nextTimes('a', 2);
		expect(stepBack(second)).toBe(first);

		showStranger('z', first, true);

		expect(canStepBack('z')).toBe(true);
		expect(stepBack('z')).toBe(first);
		// And Next carries the walk on from where it stood.
		expect(await stepForward(first)).toBe(second);
	});

	it('steps on through the walk when the open file is deleted, and never back onto it', async () => {
		openAsset('a', FIVE());
		const [first, second] = await nextTimes('a', 2);

		const to = await stepAwayFrom(second);

		expect(to).not.toBeNull();
		expect([first, second, 'a']).not.toContain(to);
		expect(stepBack(to as string)).toBe(first);
	});

	it("goes back to the list's own order when Shuffle is turned off", async () => {
		openAsset('b', FIVE());
		await nextTimes('b', 2);

		run.toggle();

		expect(run.shuffle).toBe(false);
		expect(await stepForward('b')).toBe('c');
		expect(stepBack('b')).toBe('a');
	});

	describe('turned off on a file read from beyond the page', () => {
		/* A list of a hundred with two held. The shuffled walk reads `x` from the server, so the
		   panel holds no neighbours for it in the list's own order. */
		function reader(located: number | null) {
			const inOrder = Array.from({ length: 100 }, (_, at) =>
				CLIP(at === 50 ? 'x' : at === 49 ? 'w' : at === 51 ? 'z' : `n${at}`)
			);
			const asked: number[] = [];
			const more: Continues = {
				from: 0,
				total: 100,
				shuffles: true,
				async fetch(offset, limit, order) {
					if (order !== undefined) return offset === 0 ? [CLIP('x'), CLIP('y')] : [];
					asked.push(offset);
					return inOrder.slice(offset, offset + limit);
				},
				locate: async () => located
			};
			return { more, asked };
		}

		it('keeps Next and Back immediately, and then walks from where the file really sits', async () => {
			const { more, asked } = reader(50);
			openAsset('a', [CLIP('a'), CLIP('b')], undefined, undefined, more);
			expect(await stepForward('a')).toBe('x');

			toggleShuffle('x');

			expect(run.shuffle).toBe(false);
			expect(canStepForward('x')).toBe(true);
			expect(canStepBack('x')).toBe(true);
			for (let turn = 0; turn < 6; turn++) await Promise.resolve();
			expect(asked).toEqual([20]);
			expect(neighboursOf('x')).toEqual({ previous: 'w', next: 'z' });
		});

		it('leaves it after the file the walk began on when the list cannot say where it is', async () => {
			const { more } = reader(null);
			openAsset('a', [CLIP('a'), CLIP('b')], undefined, undefined, more);
			expect(await stepForward('a')).toBe('x');

			toggleShuffle('x');
			for (let turn = 0; turn < 6; turn++) await Promise.resolve();

			expect(neighboursOf('x')).toEqual({ previous: 'a', next: 'b' });
		});
	});

	it('starts a fresh order for a new list', async () => {
		openAsset('a', FIVE());
		await nextTimes('a', 1);
		const walked = run.walk;

		openAsset('a', FIVE());

		expect(run.walk).toBeNull();
		await nextTimes('a', 1);
		expect(run.walk).not.toBe(walked);
	});
});

describe('a file reached by Randomize', () => {
	beforeEach(() => {
		openAsset('b', [CLIP('a'), CLIP('b'), PHOTO('c')]);
	});

	it('joins the list just after the file it was jumped from', () => {
		showStranger('z', 'b', true);
		expect(neighboursOf('z')).toEqual({ previous: 'b', next: 'c' });
		expect(neighboursOf('b').next).toBe('z');
	});

	it('moves nothing when it was already in the list', () => {
		showStranger('c', 'b', false);
		expect(neighboursOf('b').next).toBe('c');
		expect(neighboursOf('c')).toEqual({ previous: 'b', next: null });
	});

	it('goes first when nothing was being watched', () => {
		showStranger('z', null, true);
		expect(neighboursOf('z')).toEqual({ previous: null, next: 'a' });
	});
});

describe('a file deleted while it was open', () => {
	beforeEach(() => {
		openAsset('b', [CLIP('a'), CLIP('b'), PHOTO('c')]);
	});

	it('comes out of the list, so stepping back does not land on it', () => {
		/* Delete is on the popout's own menu, so the file the panel is built around can go while
		   somebody is looking at it. Without this the panel steps on and Previous walks straight
		   back onto a file that answers 404: "Not found" inside a dialog that was working a
		   moment ago. The list is the client's copy of what was on screen, so correcting it is
		   the client's job. */
		forgetAsset('b');

		expect(neighboursOf('b')).toEqual({ previous: null, next: null });
		expect(neighboursOf('a')).toEqual({ previous: null, next: 'c' });
		expect(neighboursOf('c')).toEqual({ previous: 'a', next: null });
	});

	it('leaves the list alone for a file that was never in it', () => {
		// A file opened from a lookalike strip on a panel that had no list behind it at all. Nothing
		// to take out is not an error, and removing the wrong row would be far worse than doing
		// nothing.
		forgetAsset('z');

		expect(neighboursOf('b')).toEqual({ previous: 'a', next: 'c' });
	});
});

describe('where a panel was opened from, for the sittings it reports', () => {
	beforeEach(() => {
		behind.route = '/browse';
		behind.url = new URL('http://sift.test/browse');
		behind.params = {};
	});

	const PERSON = '01HX0000000000000000000901';

	it('names the screen behind it, and the one thing that screen is about', () => {
		behind.route = '/people/[id]';
		behind.url = new URL(`http://sift.test/people/${PERSON}`);
		behind.params = { id: PERSON };
		openAsset('b', [CLIP('a'), CLIP('b')]);

		expect(panelPlace('b')).toMatchObject({
			screen: 'panel',
			opened_from: 'person',
			opened_from_id: PERSON,
			loop: null
		});
	});

	it('names no thing where the address does not carry an id', () => {
		behind.route = '/tags/[id]';
		behind.params = { id: 'not-an-id' };
		openAsset('b', [CLIP('b')]);

		expect(panelPlace('b')).toMatchObject({ opened_from: 'tag', opened_from_id: null });
	});

	it('calls the library with words typed into it a search, and keeps the words', () => {
		behind.url = new URL('http://sift.test/browse?q=beach');
		openAsset('b', [CLIP('b')]);

		expect(panelPlace('b')).toMatchObject({ opened_from: 'search', searched: 'beach' });
	});

	it('calls the library narrowed to one folder that folder', () => {
		behind.url = new URL(`http://sift.test/browse?in=${PERSON}`);
		openAsset('b', [CLIP('b')]);

		expect(panelPlace('b')).toMatchObject({ opened_from: 'folder', opened_from_id: PERSON });
	});

	it('calls a file opened on Insights opened from Insights, not other', () => {
		behind.route = '/insights';
		openAsset('b', [CLIP('b')]);

		expect(panelPlace('b').opened_from).toBe('insights');
	});

	it('names the thing for every screen about one thing, so a new one cannot forget', () => {
		/* A screen about one thing has its id in the address. Each is either a word that names its
		   thing, or one of the three that are not about a thing a figure counts: a file's own address,
		   an Organize queue's item, and a recap. */
		const NOT_A_THING = new Set(['link', 'organize', 'insights']);
		for (const [route, word] of Object.entries(OPENED_FROM)) {
			const aboutOne = /\[id\]$/.test(route);
			if (aboutOne) expect(NAMES_ITS_THING.has(word) || NOT_A_THING.has(word), route).toBe(true);
			if (NAMES_ITS_THING.has(word)) expect(aboutOne, route).toBe(true);
		}
		for (const word of NAMES_ITS_THING) {
			const route = Object.keys(OPENED_FROM).find((one) => OPENED_FROM[one] === word);
			behind.route = route ?? null;
			behind.params = { id: PERSON };
			expect(panelPlace('b').opened_from_id, word).toBe(PERSON);
		}
	});

	it('calls an address arrived at a link, and a screen this list does not name other', () => {
		behind.route = '/asset/[id]';
		enterAsset('b');
		expect(panelPlace('b').opened_from).toBe('link');

		behind.route = '/settings/[[section]]';
		expect(panelPlace('b').opened_from).toBe('other');
	});

	it('names the Loop only for the file it is a stretch of', () => {
		/* Stepping on through the wall of Loops is still opened from Loops, and is not that Loop
		   any more. A sitting claiming it would be a Loop's use counted on the wrong file. */
		behind.route = '/loops';
		openAsset('b', [CLIP('b'), CLIP('c')], 1_000, 4_000, null, 'the-loop');

		expect(panelPlace('b')).toMatchObject({ opened_from: 'loops', loop: 'the-loop' });
		expect(panelPlace('c')).toMatchObject({ opened_from: 'loops', loop: null });
	});

	it('forgets the Loop when an address is arrived at instead', () => {
		openAsset('b', [CLIP('b')], 1_000, 4_000, null, 'the-loop');
		enterAsset('b');

		expect(panelPlace('b').loop).toBeNull();
	});

	/* The component gallery's screens belong to its own repository, nested at `routes/design/` and
	   absent from a clone. The lists name them for the tree that has them; only that tree is asked. */
	const GALLERY = '/design';
	const inGallery = (route: string) => route === GALLERY || route.startsWith(`${GALLERY}/`);
	const screens = Object.keys(import.meta.glob('/src/routes/**/+page.svelte')).map(
		(file) => file.replace('/src/routes', '').replace(/\/\+page\.svelte$/, '') || '/'
	);

	/** On one side of that line: the screens neither list names, and the names that are no screen. */
	function unnamedAndGone(gallery: boolean): { unnamed: string[]; gone: string[] } {
		const routes = screens.filter((route) => inGallery(route) === gallery);
		const named = [...Object.keys(OPENED_FROM), ...OPENS_NO_FILE].filter(
			(route) => inGallery(route) === gallery
		);
		return {
			unnamed: routes.filter((route) => !named.includes(route)),
			gone: named.filter((route) => !routes.includes(route))
		};
	}

	it('knows every screen: each is named, or declared to show no file of its own', () => {
		/* A new screen that can open a file has to be given its word when it is added, or every
		   sitting from it reads `other` for ever and nobody is told. */
		expect(unnamedAndGone(false)).toEqual({ unnamed: [], gone: [] });
		expect(Object.keys(OPENED_FROM).filter((route) => OPENS_NO_FILE.includes(route))).toEqual([]);
	});

	it.skipIf(!screens.includes(GALLERY))(
		'knows the gallery screens too, where the gallery is in the tree',
		() => {
			expect(unnamedAndGone(true)).toEqual({ unnamed: [], gone: [] });
		}
	);
});
