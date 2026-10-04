/* A cell finding itself something to play, and stopping honestly when there is nothing left.
 *
 * The interesting part of a cell is not the picture. It is the run: filling it from a query,
 * moving through it, going back, and what happens at the end of a file. None of that needs a DOM,
 * and all of it is the half that fails quietly.
 *
 * The server is stood in for, deliberately. What is under test is what the cell DOES with an answer:
 * that it plays what it is given in the order given, that it prefers files this browser can play without the one
 * transcoder the install has, that it says which of the two empty states it is in, and that letting
 * go really lets go.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { api, ApiError } from '$lib/api/client';
import { RANDOM } from '$lib/grid/sort-state.svelte';
import { Cell, PLAN_UNASKED } from './cell.svelte';
import { loudness } from '$lib/player/loudness.svelte';

/** One tile, as `/assets` sends it. The size is on the row, which is where a cell reads it from. */
function file(id: string) {
	return {
		id,
		media_type: 'video',
		duration_ms: 1000,
		thumb: true,
		art: null,
		original_filename: null,
		favorite: false,
		rating: null,
		concealed: false,
		width: 1920,
		height: 1080
	};
}

/** A page of them, as the route answers. */
function page(ids: string[], total = ids.length) {
	return { items: ids.map(file), total };
}

/** What the playback route says about a file. `direct` unless a route is asked for. */
function plan(route = 'direct', streamable = true) {
	return { route, streamable, url: '/stream', reason: '', scale_height: null, resume_ms: null };
}

let served: Record<string, unknown> = {};
let plans: Record<string, unknown> = {};
let asked: string[] = [];

beforeEach(() => {
	served = {};
	plans = {};
	asked = [];

	vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
		// The run is filled from the ordinary asset page, and the detail is only ever asked for the
		// file that has actually reached the screen.
		if (path === '/assets') return served.page ?? page([]);
		return { sprite: null };
	});

	vi.spyOn(api, 'post').mockImplementation(async (path: string) => {
		const id = path.split('/')[2];
		asked.push(id);
		return plans[id] ?? plan();
	});
});

describe('a file somebody pressed', () => {
	it('stays pressed when the server would not say how to play it, and says so', async () => {
		vi.spyOn(api, 'post').mockRejectedValue(new Error('the ask timed out'));
		const cell = new Cell();

		await cell.takeOver({ ...new Cell().saved, source: '' }, file('z'));

		expect(cell.state).toBe('failed');
		expect(cell.problem).toBe(PLAN_UNASKED);
		// Asked twice before giving up: once is a blip, twice is an answer.
		expect(vi.mocked(api.post)).toHaveBeenCalledTimes(2);
		expect(cell.playing, 'nothing else was put in its place').toBeNull();
	});

	it('is stepped past when it is gone, which is what a 404 says', async () => {
		vi.spyOn(api, 'post').mockRejectedValue(new ApiError(404, 'gone'));
		served.page = page([]);
		const cell = new Cell();

		await cell.takeOver({ ...new Cell().saved, source: '' }, file('z'));

		expect(cell.problem).toBeNull();
		expect(cell.state).not.toBe('failed');
	});
});

describe('filling a run', () => {
	/*
	 * A PAGE THAT NEVER ANSWERS MUST NOT STOP THE CELL.
	 *
	 * An advance waits on a lookahead still out, the lookahead waits on its page, and a page with
	 * no deadline, queued behind the browser's six connections to one origin (which a wall of up
	 * to nine live streams is exactly the case for) would hold the cell on the last frame of the
	 * file that had just ended, for as long as the wall was open. The plan has a deadline for that
	 * reason; the page is the same ask. A timeout's clock is the runtime's own rather than one a
	 * fake timer moves, so the wiring is what is held: the ask carries a signal, and a page that is
	 * abandoned leaves the cell saying so rather than waiting.
	 */
	it('gives the page a deadline, and moves on when it is abandoned', async () => {
		let signal: AbortSignal | undefined;
		vi.spyOn(api, 'get').mockImplementation(
			async (path: string, options?: { signal?: AbortSignal }) => {
				if (path !== '/assets') return { sprite: null };
				signal = options?.signal;
				throw new DOMException('The operation timed out.', 'TimeoutError');
			}
		);
		const cell = new Cell();

		await cell.restart();

		expect(signal, 'the page was asked for with no deadline').toBeInstanceOf(AbortSignal);
		expect(cell.state, 'an abandoned page left the cell waiting').toBe('nothing_here');
	});

	it('plays what the source holds', async () => {
		served.page = page(['a', 'b', 'c']);
		const cell = new Cell();
		cell.orderBy(null);

		await cell.restart();

		expect(cell.playing?.id).toBe('a');
		expect(cell.state).toBe('ready');
	});

	it('narrows to things that move unless the cell was told otherwise', () => {
		const cell = new Cell();
		expect(cell.query.media).toBe('video|gif');

		cell.mediaKind = 'all';
		expect(
			cell.query.media,
			'a cell showing everything still asked for video only'
		).toBeUndefined();
	});

	it('carries the source as a typed query, and nothing at all when there is none', () => {
		const cell = new Cell();
		expect(cell.query.q).toBeUndefined();

		cell.source = '  people:Jane  ';
		expect(cell.query.q).toBe('people:Jane');
	});

	/*
	 * ASKING A CELL TO BE SHUFFLED, which is an order plus the one thing an order does not normally
	 * carry: WHICH shuffle.
	 *
	 * A cell holds a seed for the life of the run, which is all a seed has to survive. See
	 * `orders.ts`. What is held here is the pair a reader could not check: that one shuffle holds
	 * across the pages of one run, and that asking again is a different one.
	 */
	describe('being put in a shuffled order', () => {
		it('mints a shuffle with the order, and sends both', () => {
			const cell = new Cell();
			cell.orderBy('random');

			expect(cell.sort).toBe('random');
			expect(cell.query.sort).toBe('random');
			expect(cell.query.seed, 'the run would be a different shuffle on every page').toBeDefined();
		});

		it('holds that one shuffle until it is asked for another', () => {
			const cell = new Cell();
			cell.orderBy('random');
			const first = cell.query.seed;

			expect(cell.query.seed, 'reading the query twice minted twice').toBe(first);

			const seeds = new Set([first]);
			for (let press = 0; press < 6; press += 1) {
				cell.orderBy('random');
				seeds.add(cell.query.seed);
			}
			expect(seeds.size, 'asking to be shuffled again gave back the same shuffle').toBeGreaterThan(
				1
			);
		});

		it('retires the shuffle with the order', () => {
			/* A seed beside any other order describes an arrangement nobody is looking at. The server
			   ignores it, so sending one puts a number in the request that says nothing at all. */
			const cell = new Cell();
			cell.orderBy('random');
			cell.orderBy('newest');

			expect(cell.seed).toBeNull();
			expect(cell.query.seed).toBeUndefined();
		});

		it('mints afresh for a wall taken back out of a saved preset', () => {
			/* The order is saved and the seed is not, which is the right way round: a wall put away
			   shuffled is asking to be shuffled, not to be given back last week's arrangement. */
			const cell = new Cell();
			const before = cell.seed;
			cell.adopt({
				source: '',
				media_kind: 'video_gif',
				ordering: 'shuffle',
				end_behaviour: 'loop_all',
				timer_seconds: null,
				volume: 100,
				sort: 'random',
				aspect: 'dynamic'
			});

			expect(cell.sort).toBe('random');
			expect(cell.seed, 'a saved preset came back on the shuffle the cell already had').not.toBe(
				before
			);
		});
	});

	/*
	 * SHUFFLE IS RANDOM OVER THE WHOLE SOURCE, with a seed of its own per cell.
	 *
	 * Jumbling each page of sixty newest files among themselves, with every fresh cell asking the
	 * same question, would have four cells playing the same sixty files. A page is the next page of
	 * the cell's own seeded shuffle, continued after the last row it was given.
	 */
	describe('shuffling the whole source', () => {
		function recording(pages: ReturnType<typeof page>[]) {
			const queries: Record<string, string>[] = [];
			vi.spyOn(api, 'get').mockImplementation(
				async (path: string, options?: { query?: Record<string, unknown> }) => {
					if (path !== '/assets') return { sprite: null };
					queries.push((options?.query ?? {}) as Record<string, string>);
					return pages[Math.min(queries.length - 1, pages.length - 1)];
				}
			);
			return queries;
		}

		it('starts shuffled, each cell with a seed of its own', () => {
			const one = new Cell();
			const two = new Cell();

			expect(one.ordering).toBe('shuffle');
			expect(one.query.sort).toBe(RANDOM);
			expect(one.query.seed, 'two fresh cells asked for the same shuffle').not.toBe(two.query.seed);
		});

		it('plays the page in the order the server gave, and continues after its last row', async () => {
			const queries = recording([page(['c', 'a', 'b'], 200), page(['d', 'e'], 200)]);
			const cell = new Cell();

			await cell.restart();
			expect(cell.playing?.id, 'the page was jumbled on the way in').toBe('c');
			await cell.advance();

			expect(queries[0].offset).toBe('0');
			expect(queries[1].after, 'the next page was not continued from the last row').toBe('b');
			expect(queries[1].offset).toBeUndefined();
			expect(queries[1].seed, 'one run changed its shuffle between pages').toBe(queries[0].seed);
		});

		it('continues an order the server can seek after its last row too, never from an offset', async () => {
			/* An offset walks every row before it: a deep page of a large library costs seconds where
			   a page continued from a row costs one seek. */
			const queries = recording([page(['c', 'a', 'b'], 200), page(['d', 'e'], 200)]);
			const cell = new Cell();
			cell.orderBy('newest');

			await cell.restart();
			await cell.advance();
			await cell.advance();
			await cell.advance();

			expect(queries[1].after).toBe('b');
			expect(queries[1].offset).toBeUndefined();
		});

		it('keeps the offset for an order the server cannot seek', async () => {
			const queries = recording([page(['c', 'a', 'b'], 200), page(['d', 'e'], 200)]);
			const cell = new Cell();
			cell.orderBy('relevance');

			await cell.restart();
			await cell.advance();
			await cell.advance();
			await cell.advance();

			expect(queries[1].after).toBeUndefined();
			expect(queries[1].offset).toBe('3');
		});

		it('draws a fresh shuffle when it comes round to the start of its source again', async () => {
			const queries = recording([page(['a', 'b'])]);
			const cell = new Cell();

			await cell.restart();
			await cell.advance();
			await cell.advance();

			const last = queries[queries.length - 1];
			expect(last.after, 'the second time round continued past the end').toBeUndefined();
			expect(last.seed, 'the second time round was the first order again').not.toBe(
				queries[0].seed
			);
		});

		/*
		 * A SMALL SOURCE COMES ROUND IN A DIFFERENT ORDER. The server here deals the same order
		 * every time, which is what a fresh seed over two files does half the time: the case where a
		 * wall would play the first round again.
		 */
		it('a two-file cell alternates: the clip that just ended never plays twice running', async () => {
			recording([page(['a', 'b'])]);
			const cell = new Cell();
			const played: (string | undefined)[] = [];

			await cell.restart();
			played.push(cell.playing?.id);
			for (let step = 0; step < 5; step += 1) {
				await cell.advance();
				played.push(cell.playing?.id);
			}

			expect(played, 'a clip played twice running').toEqual(['a', 'b', 'a', 'b', 'a', 'b']);
		});

		it('does not open the next round on the file that has just ended, where it can avoid it', async () => {
			recording([page(['a', 'b', 'c'])]);
			const cell = new Cell();
			const played: (string | undefined)[] = [];

			await cell.restart();
			played.push(cell.playing?.id);
			for (let step = 0; step < 5; step += 1) {
				await cell.advance();
				played.push(cell.playing?.id);
			}

			expect(played.slice(0, 3)).toEqual(['a', 'b', 'c']);
			expect(played.slice(3), 'the second round was the first again').not.toEqual(['a', 'b', 'c']);
			expect(played[3], 'the file that had just ended played twice running').not.toBe('c');
		});

		/*
		 * A SOURCE HOLDING FILES WITH NOTHING TO PLAY: say a tag holding three playable videos among
		 * six. The page comes back in a different order and the cell steps over the gone files, so
		 * judged on the deal, what is SHOWN can be the same round again, or the file that just ended.
		 */
		it('judges the next round on the files it plays, stepping over the gone ones', async () => {
			recording([page(['x', 'a', 'y', 'b', 'c'])]);
			vi.spyOn(api, 'post').mockImplementation(async (path: string) => {
				const id = path.split('/')[2];
				if (id === 'x' || id === 'y') throw new ApiError(404, 'gone');
				return plan();
			});
			const cell = new Cell();
			const played: (string | undefined)[] = [];

			await cell.restart();
			played.push(cell.playing?.id);
			for (let step = 0; step < 5; step += 1) {
				await cell.advance();
				played.push(cell.playing?.id);
			}

			expect(played.slice(0, 3)).toEqual(['a', 'b', 'c']);
			expect(played.slice(3), 'the second round showed the first again').not.toEqual([
				'a',
				'b',
				'c'
			]);
			expect(played[3], 'the file that had just ended played twice running').not.toBe('c');
			expect([...played.slice(3)].sort(), 'a gone file was shown').toEqual(['a', 'b', 'c']);
		});

		/*
		 * AND WHERE THE BROWSER CANNOT PLAY THEM ALL AS THEY ARE: a preference for direct play that
		 * dropped the files it passed over would never show one of the three and bring the other two
		 * round in one order for ever. Every mix of the two routes is held to the same three rules.
		 */
		it.each([
			['none of them plays as it is', ['a', 'b', 'c'], ['x', 'a', 'y', 'b', 'z', 'c']],
			['one of them has to be converted', ['a'], ['x', 'a', 'y', 'b', 'z', 'c']],
			/* Dealt b a c, shown b c a: judged on the deal, b a c again would be "a new order". */
			['the converted one is dealt second', ['a'], ['x', 'b', 'y', 'a', 'z', 'c']],
			['only one of them plays as it is', ['b', 'c'], ['x', 'a', 'y', 'b', 'z', 'c']]
		])(
			'shows every file each round, in a new order, never one twice running, where %s',
			async (_, converted, dealt) => {
				recording([page(dealt)]);
				vi.spyOn(api, 'post').mockImplementation(async (path: string) => {
					const id = path.split('/')[2];
					if (id === 'x' || id === 'y' || id === 'z') throw new ApiError(404, 'gone');
					return plan(converted.includes(id) ? 'transcode' : 'direct');
				});
				const cell = new Cell();
				const played: (string | undefined)[] = [];

				await cell.restart();
				played.push(cell.playing?.id);
				for (let step = 0; step < 11; step += 1) {
					await cell.advance();
					played.push(cell.playing?.id);
				}

				const rounds = [0, 3, 6, 9].map((at) => played.slice(at, at + 3));
				for (const round of rounds) {
					expect([...round].sort(), `a file was left out: ${played}`).toEqual(['a', 'b', 'c']);
				}
				for (let at = 1; at < rounds.length; at += 1) {
					expect(rounds[at], `a round came round in the same order: ${played}`).not.toEqual(
						rounds[at - 1]
					);
				}
				for (let at = 1; at < played.length; at += 1) {
					expect(played[at], `a clip played twice running: ${played}`).not.toBe(played[at - 1]);
				}
			}
		);

		/*
		 * A round dealt with the clip on screen first. Two orders over the same files cannot both
		 * avoid it when only one of them plays as it is, and a next page can simply open on it.
		 */
		it('never opens what comes next on the clip that has just ended, where there is anything else', async () => {
			recording([page(['a', 'b']), page(['b', 'c'])]);
			const cell = new Cell();
			const played: (string | undefined)[] = [];

			await cell.restart();
			played.push(cell.playing?.id);
			for (let step = 0; step < 3; step += 1) {
				await cell.advance();
				played.push(cell.playing?.id);
			}

			expect(played.slice(0, 2)).toEqual(['a', 'b']);
			expect(played[2], 'the clip that had just ended played twice running').not.toBe('b');
			expect(played.slice(2).sort(), 'a file was left out of the round').toEqual(['b', 'c']);
		});

		it('steps over a file another cell is showing, and plays it only when nothing else is left', async () => {
			served.page = page(['a', 'b']);
			const cell = new Cell();
			cell.elsewhere = () => new Set(['a']);

			await cell.restart();
			expect(cell.playing?.id, 'two cells showed one file').toBe('b');

			served.page = page(['a']);
			await cell.restart();
			expect(cell.playing?.id, 'a blank cell beat a file shown elsewhere').toBe('a');
		});
	});

	it('says so when the source matches nothing this account may see', async () => {
		served.page = page([]);
		const cell = new Cell();

		await cell.restart();

		expect(cell.state).toBe('nothing_here');
		expect(cell.playing).toBeNull();
	});
});

describe('what happens at the end of a file', () => {
	it('stops, and says it has stopped', async () => {
		served.page = page(['a', 'b']);
		const cell = new Cell();
		cell.orderBy(null);
		cell.endBehaviour = 'once';
		await cell.restart();

		await cell.ended();

		expect(cell.state, 'a cell set to stop moved on instead').toBe('stopped');
		expect(cell.playing?.id, 'the file it stopped on was let go').toBe('a');
	});

	/*
	 * "Repeat this" has to DO something.
	 *
	 * The element's own `loop` is deliberately off (only the cell knows whether "again" means
	 * this file or the next), so a cell that answered `loop_one` by returning without doing
	 * anything would leave the file frozen on its last frame, indistinguishable from "Stop at the
	 * end". Checking that the same file is still in place is true of a cell that has done nothing
	 * at all, so this checks that it plays again.
	 */
	it('plays the same file again when it is set to repeat', async () => {
		served.page = page(['a', 'b']);
		const cell = new Cell();
		cell.orderBy(null);
		cell.endBehaviour = 'loop_one';
		await cell.restart();
		let again = 0;
		cell.replay = () => (again += 1);

		await cell.ended();

		expect(again, 'the end of the file was reached and nothing started it again').toBe(1);
		expect(cell.playing?.id, 'repeat moved the cell on').toBe('a');
		expect(cell.state).toBe('ready');
	});

	it('does not ask for a replay on any other end-behaviour', async () => {
		served.page = page(['a', 'b']);
		const cell = new Cell();
		cell.orderBy(null);
		cell.endBehaviour = 'loop_all';
		await cell.restart();
		let again = 0;
		cell.replay = () => (again += 1);

		await cell.ended();

		expect(again).toBe(0);
		expect(cell.playing?.id).toBe('b');
	});

	it('moves on when it is set to play through', async () => {
		served.page = page(['a', 'b']);
		const cell = new Cell();
		cell.orderBy(null);
		cell.endBehaviour = 'loop_all';
		await cell.restart();

		await cell.ended();

		expect(cell.playing?.id).toBe('b');
	});

	/*
	 * Play through at the end of the source goes round again. Stopping is one way to avoid a black
	 * rectangle, but it would make "Play through" and "Stop at the end" the same thing, and a wall
	 * left running a wall of still frames. The guard that matters (a cell never goes quiet with
	 * nothing on screen saying why) is the case below it.
	 */
	it('starts the run again once the source is exhausted, when it plays through', async () => {
		served.page = page(['a']);
		const cell = new Cell();
		cell.orderBy(null);
		cell.endBehaviour = 'loop_all';
		await cell.restart();

		await cell.ended();

		expect(cell.state, 'play through means play through, not stop at the end').toBe('ready');
		expect(cell.playing?.id).toBe('a');
	});

	it('stops at the end when that is what it was told to do', async () => {
		served.page = page(['a', 'b']);
		const cell = new Cell();
		cell.orderBy(null);
		cell.endBehaviour = 'once';
		await cell.restart();

		await cell.ended();

		expect(cell.state).toBe('stopped');
	});

	/* The rewind is guarded on something having played, or a cell pointed at a query matching
	   nothing would ask the same empty question for ever, and the sentence it owes somebody is
	   about the filter rather than about the end of a run. */
	it('says the filter matches nothing rather than rewinding an empty run', async () => {
		served.page = page([]);
		const cell = new Cell();
		cell.endBehaviour = 'loop_all';
		await cell.restart();

		expect(cell.state).toBe('nothing_here');
	});
});

describe('what shape a cell is', () => {
	it('is the shape of what it is playing, while nothing has been chosen', async () => {
		served.page = page(['a']);
		const cell = new Cell();
		await cell.restart();

		expect(cell.aspect).toBe('dynamic');
		expect(cell.shape).toBeCloseTo(16 / 9);
	});

	it('is the shape that was chosen, whatever it is playing', async () => {
		served.page = page(['a']);
		const cell = new Cell();
		await cell.restart();

		cell.aspect = 'tall';

		expect(cell.shape, 'choosing a shape is what choosing a shape means').toBeCloseTo(9 / 16);
	});

	it('has no shape at all when it is Dynamic with nothing playing', () => {
		// The caller assumes the commonest shape until there is an answer, which costs one reflow
		// rather than a wrong layout. It cannot do that if this invents a number.
		expect(new Cell().shape).toBeNull();
	});
});

describe('a file Sift has not read', () => {
	it('is stepped over, because there is nothing to attach yet', async () => {
		served.page = page(['a', 'b']);
		plans.a = plan('unread');
		const cell = new Cell();
		cell.orderBy(null);
		await cell.restart();

		expect(asked).toEqual(['a', 'b']);
		expect(cell.playing?.id).toBe('b');
	});
});

describe('a file this browser cannot play', () => {
	it('steps over it when the cell plays through', async () => {
		served.page = page(['a', 'b']);
		const cell = new Cell();
		cell.orderBy(null);
		cell.endBehaviour = 'loop_all';
		await cell.restart();

		await cell.failed();

		expect(cell.playing?.id, 'one bad file must not end an unattended wall').toBe('b');
	});

	it('holds still on it when the cell was told to stop at the end', async () => {
		served.page = page(['a', 'b']);
		const cell = new Cell();
		cell.orderBy(null);
		cell.endBehaviour = 'once';
		await cell.restart();

		await cell.failed();

		expect(cell.state).toBe('failed');
		expect(cell.playing?.id).toBe('a');
	});

	it('gives up after a run of them rather than sprinting through the library', async () => {
		served.page = page(['a', 'b', 'c', 'd', 'e', 'f']);
		const cell = new Cell();
		cell.orderBy(null);
		cell.endBehaviour = 'loop_all';
		await cell.restart();

		// Every file fails, one after another, with nothing playing in between.
		await cell.failed();
		await cell.failed();
		await cell.failed();

		expect(cell.state).toBe('failed');
		expect(cell.playing?.id, 'the third failure is where it stops trying').toBe('c');
	});

	it('forgets the run of failures the moment something plays', async () => {
		served.page = page(['a', 'b', 'c', 'd', 'e', 'f']);
		const cell = new Cell();
		cell.orderBy(null);
		cell.endBehaviour = 'loop_all';
		await cell.restart();

		await cell.failed();
		await cell.failed();
		cell.started();
		await cell.failed();
		await cell.failed();

		expect(cell.state, 'a failure is only a run while nothing plays between them').toBe('ready');
	});
});

describe('going back through what has been shown', () => {
	it('offers nothing to go back to on the first file', async () => {
		served.page = page(['a', 'b']);
		const cell = new Cell();
		cell.orderBy(null);
		await cell.restart();

		expect(cell.hasBack).toBe(false);
	});

	it('returns to the one before', async () => {
		served.page = page(['a', 'b']);
		const cell = new Cell();
		cell.orderBy(null);
		await cell.restart();
		await cell.advance();
		expect(cell.playing?.id).toBe('b');

		await cell.back();

		expect(cell.playing?.id).toBe('a');
	});
});

describe('what a cell will wait for', () => {
	it('prefers a file this browser can play without the one transcoder', async () => {
		served.page = page(['needs-work', 'plays-here']);
		plans['needs-work'] = plan('transcode');
		const cell = new Cell();
		cell.orderBy(null);

		await cell.restart();

		expect(cell.playing?.id, 'a wall shuffling into transcodes queues behind itself').toBe(
			'plays-here'
		);
	});

	it('takes what it can get rather than searching forever', async () => {
		const ids = ['one', 'two', 'three', 'four', 'five'];
		served.page = page(ids);
		for (const id of ids) plans[id] = plan('transcode');
		const cell = new Cell();
		cell.orderBy(null);

		await cell.restart();

		expect(
			cell.playing,
			'a library of files that all need converting played nothing'
		).not.toBeNull();
	});

	it('says it is waiting when the file will not keep up, rather than showing nothing', async () => {
		served.page = page(['slow']);
		plans.slow = plan('transcode', false);
		const cell = new Cell();

		await cell.restart();

		expect(cell.state).toBe('waiting');
		expect(cell.playing?.id).toBe('slow');
	});

	it('skips a file that has gone since the run was filled', async () => {
		served.page = page(['gone', 'here']);
		plans.gone = null;
		vi.spyOn(api, 'post').mockImplementation(async (path: string) => {
			const id = path.split('/')[2];
			asked.push(id);
			if (id === 'gone') throw new Error('not found');
			return plan();
		});
		const cell = new Cell();
		cell.orderBy(null);

		await cell.restart();

		expect(cell.playing?.id).toBe('here');
	});
});

describe('letting go', () => {
	it('drops the picture, the run and the marks together', async () => {
		served.page = page(['a', 'b', 'c']);
		const cell = new Cell();
		cell.orderBy(null);
		await cell.restart();
		cell.loop.mark('a', 1);
		cell.loop.mark('a', 5);

		cell.release();

		expect(cell.playing).toBeNull();
		expect(cell.plan).toBeNull();
		expect(cell.state).toBe('empty');
		expect(cell.loop.running, 'the marks outlived the file they were set on').toBe(false);
	});

	it('does not play what it was holding when it fills again', async () => {
		/* The vault case. A relock has to drop the ids the run is carrying, not only the picture:
		 * otherwise the very next advance plays one of them. */
		served.page = page(['secret-one', 'secret-two']);
		const cell = new Cell();
		cell.orderBy(null);
		await cell.restart();

		cell.release('loading');
		served.page = page([]);
		await cell.advance();

		expect(
			cell.playing,
			'a cell played an id it was holding from before the vault shut'
		).toBeNull();
	});

	it('forgets what it had shown, so back leads nowhere', async () => {
		served.page = page(['a', 'b']);
		const cell = new Cell();
		cell.orderBy(null);
		await cell.restart();
		await cell.advance();

		cell.release();

		expect(cell.hasBack).toBe(false);
	});
});

describe('changing what a cell draws from', () => {
	it('throws the old run away rather than adding to it', async () => {
		served.page = page(['old-one', 'old-two']);
		const cell = new Cell();
		cell.orderBy(null);
		await cell.restart();

		served.page = page(['new-one', 'new-two']);
		cell.source = 'people:Jane';
		await cell.restart();

		expect(cell.playing?.id).toBe('new-one');
		await cell.advance();
		expect(cell.playing?.id, 'a cell kept playing files from the filter it had left').toBe(
			'new-two'
		);
	});

	it('reads a saved cell, and refuses a word this version does not know', () => {
		const cell = new Cell();

		cell.adopt({
			source: 'fav:yes',
			media_kind: 'hologram',
			ordering: 'backwards',
			end_behaviour: 'forever',
			timer_seconds: 30,
			volume: 250
		} as never);

		expect(cell.source).toBe('fav:yes');
		expect(cell.mediaKind).toBe('video_gif');
		expect(cell.sort, 'a saved cell with no order of its own walks its source in order').toBeNull();
		expect(cell.ordering).toBe('in_order');
		expect(cell.endBehaviour, 'a behaviour nothing here understands reached a cell').toBe(
			'loop_all'
		);
		expect(cell.volume).toBe(100);
	});
});

describe('changing the source while a request is still out', () => {
	it('does not report the new one as empty because the old one was still asking', async () => {
		/* The flag that stops a drain and a refill both asking is per era, not a plain boolean: a
		 * fill left in flight by a source somebody had already changed would otherwise keep it
		 * set, and the NEW source's first fill would return without asking for anything. The cell
		 * would then find an empty run and say the filter matches nothing this account may see:
		 * a sentence about permissions for what was a race.
		 */
		let release: (value: unknown) => void = () => {};
		const held = new Promise((resolve) => (release = resolve));
		let first = true;

		vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
			if (path !== '/assets') return { sprite: null };
			if (first) {
				first = false;
				await held;
				return page(['old-one']);
			}
			return page(['new-one']);
		});

		const cell = new Cell();
		cell.orderBy(null);
		const stale = cell.restart();

		cell.source = 'people:Jane';
		const fresh = cell.restart();
		release(null);
		await Promise.all([stale, fresh]);

		expect(cell.state, 'a race was reported as a filter that matches nothing').not.toBe(
			'nothing_here'
		);
		expect(cell.playing?.id).toBe('new-one');
	});
});

describe("a cell's sitting with a file", () => {
	it('is one sitting however many times the file repeats or the cell is drawn again', () => {
		/* The element says it is playing every time round, and the wall is drawn afresh when it
		 * is handed to the corner panel. Both are the same sitting carrying on, and the redraw
		 * is the case that would otherwise stop the file being timed at all.
		 */
		const cell = new Cell();
		const first = cell.sittingWith('a');
		first.reported = 4_000;

		const again = cell.sittingWith('a');

		expect(again.id).toBe(first.id);
		expect(again.reported, 'a redraw forgot what was already reported').toBe(4_000);
	});

	it('begins a new sitting for the next file, with nothing reported yet', () => {
		const cell = new Cell();
		const first = cell.sittingWith('a');

		const next = cell.sittingWith('b');

		expect(next.id).not.toBe(first.id);
		expect(next.file).toBe('b');
		expect(next.reported).toBeNull();
	});

	it('begins a new sitting when the cell comes back to a file after others', () => {
		const cell = new Cell();
		const first = cell.sittingWith('a');
		cell.sittingWith('b');

		expect(cell.sittingWith('a').id, 'coming back to a file really is another sitting').not.toBe(
			first.id
		);
	});
});

describe('changing the order', () => {
	/*
	 * SHUFFLE MUST NOT CUT OFF WHAT IS PLAYING.
	 *
	 * Through `restart`, which throws the current file away and picks another, Shuffle would do
	 * exactly what the `casino` control two places along the same drawer does, and the thing people
	 * want ("carry on, but wander from here") could not be asked for at all.
	 */
	it('leaves the file on screen alone', async () => {
		served.page = page(['a', 'b', 'c']);
		const cell = new Cell();
		cell.orderBy(null);
		await cell.restart();
		const on = cell.playing?.id;
		expect(on, 'nothing was playing to begin with').toBe('a');

		cell.orderBy(RANDOM);
		await cell.reorder();

		expect(cell.playing?.id, 'changing the order changed what was on screen').toBe(on);
		expect(cell.state).toBe('ready');
	});

	/* And it does line the run up again, or the change would mean nothing until the run ran out. */
	it('lines the run up again from the start of the source', async () => {
		served.page = page(['a', 'b', 'c']);
		const cell = new Cell();
		cell.orderBy(null);
		await cell.restart();
		await cell.advance();
		expect(cell.playing?.id).toBe('b');

		await cell.reorder();
		await cell.advance();

		// The queue was rebuilt from the top of the source, so the next file is its first again,
		// rather than `c`, which is what was left of the run the reorder replaced.
		expect(cell.playing?.id).toBe('a');
	});

	/*
	 * A DRAW ASKED FOR IS A DRAW LANDED ON, which is the one thing `reorder` cannot do for it.
	 *
	 * Rebuilding the run under the file on screen is right for every order that says something
	 * about what comes next. A shuffle says nothing about what comes next that anybody can check:
	 * asking for Shuffle again does not change the order at all, only WHICH draw, so a cell that
	 * went on showing the file the old draw had put it on would be a control that provably did
	 * nothing: a request under a fresh seed and a picture that never moves. See
	 * `$lib/theater/orders`, where the screen's half of this is decided.
	 */
	it('shows the front of the fresh draw when it is shuffled again', async () => {
		/* The stood-in server answers a DIFFERENT arrangement for each seed, because that is what a
		   seed is for: an unseeded shuffle re-draws per request and would prove nothing here. */
		const arrangements = [page(['a', 'b']), page(['b', 'a'])];
		const seeds: string[] = [];
		vi.spyOn(api, 'get').mockImplementation(
			async (path: string, options?: { query?: Record<string, unknown> }) => {
				if (path !== '/assets') return { sprite: null };
				const seed = String(options?.query?.seed ?? '');
				if (!seeds.includes(seed)) seeds.push(seed);
				return arrangements[seeds.indexOf(seed) % arrangements.length];
			}
		);

		const cell = new Cell();
		cell.orderBy(null);
		cell.orderBy(RANDOM);
		const drawn = cell.seed;
		await cell.restart();
		expect(cell.playing?.id, 'the first draw put nothing on screen').toBe('a');

		cell.orderBy(RANDOM);
		expect(cell.seed, 'asking to be shuffled again gave back the same draw').not.toBe(drawn);
		await cell.restart();

		expect(cell.playing?.id, 'the cell stayed on what the draw it replaced had chosen').toBe('b');
	});
});

describe('playing something else', () => {
	/*
	 * THE CASINO CONTROL'S PROMISE, which a bare restart cannot keep.
	 *
	 * A cell in Random order, asked six times for something else, must not send `sort=random` with
	 * the SAME seed and `offset=0` every time: that re-cuts the first sixty rows of one
	 * permutation for ever, and the first press puts back the file that was already on screen. The
	 * two halves are tested apart because they fail apart: a fresh draw that happens to begin on
	 * the same file is still a press that did nothing.
	 */
	it('draws a fresh shuffle, rather than the top of the one it was already in', async () => {
		const seeds: string[] = [];
		vi.spyOn(api, 'get').mockImplementation(
			async (path: string, options?: { query?: Record<string, unknown> }) => {
				if (path !== '/assets') return { sprite: null };
				seeds.push(String(options?.query?.seed ?? 'none'));
				return page(['a', 'b', 'c']);
			}
		);

		const cell = new Cell();
		cell.orderBy(null);
		cell.orderBy(RANDOM);
		await cell.restart();
		await cell.somethingElse();
		await cell.somethingElse();

		expect(seeds.length, 'the run was not refilled on each press').toBe(3);
		expect(new Set(seeds).size, 'every press asked for the same shuffle back').toBe(3);
		expect(seeds).not.toContain('none');
	});

	it('leaves out the file that was on screen', async () => {
		served.page = page(['a', 'b', 'c']);
		const cell = new Cell();
		cell.orderBy(null);

		await cell.restart();
		expect(cell.playing?.id).toBe('a');

		await cell.somethingElse();

		expect(cell.playing?.id, 'the press put back what it was already showing').not.toBe('a');
	});

	it('keeps it where the fresh run holds nothing else', async () => {
		/* A preference rather than a rule, and the same reading `/assets/random` gives the word: a
		   set with one file in it has nowhere else to go, and showing it again is a better answer
		   than an empty cell. */
		served.page = page(['only']);
		const cell = new Cell();
		cell.orderBy(null);

		await cell.restart();
		await cell.somethingElse();

		expect(cell.playing?.id).toBe('only');
		expect(cell.state).toBe('ready');
	});

	it('is a plain restart everywhere else, so nothing is left out of a run nobody was watching', async () => {
		served.page = page(['a', 'b', 'c']);
		const cell = new Cell();
		cell.orderBy(null);

		await cell.restart();
		await cell.restart();

		expect(cell.playing?.id, 'a restart threw away the front of its own source').toBe('a');
	});
});

describe('an advance that is already in flight when the order changes', () => {
	/*
	 * WHAT THE ERA IS FOR.
	 *
	 * `reorder` keeps what is on screen and rebuilds the run underneath it, so an `advance` that
	 * was already choosing from the OLD run must not land afterwards. It would put a file from the
	 * order somebody has just replaced onto the screen, one step after the change, which reads as
	 * the new order being ignored exactly once.
	 *
	 * The bump is the guard, and every `await` inside `advance` is followed by a re-read of it. The
	 * two tests below suspend the advance in the two places it can be suspended.
	 */

	/** A promise this test decides when to settle, so an await can be held open. */
	function held<T>() {
		let release!: (value: T) => void;
		const promise = new Promise<T>((resolve) => (release = resolve));
		return { promise, release };
	}

	it('does not land after the run has been rebuilt under it', async () => {
		served.page = page(['a', 'b', 'c']);
		const cell = new Cell();
		cell.orderBy(null);
		await cell.restart();
		expect(cell.playing?.id, 'nothing was playing to begin with').toBe('a');

		// The plan for the next file is asked for over the wire, which is where an advance waits.
		const stuck = held<Record<string, unknown>>();
		vi.spyOn(api, 'post').mockImplementation(() => stuck.promise);

		const advancing = cell.advance();
		await cell.reorder();
		stuck.release(plan());
		await advancing;

		expect(
			cell.playing?.id,
			'the advance chose from the run that had already been thrown away'
		).toBe('a');
	});

	it('does not land after a refill it started has been overtaken either', async () => {
		/* The other await, and the earlier one: `advance` fills the run first when it is running
		   low, and the reorder can arrive while that page is still on the wire. Both re-reads
		   are needed: one of them alone leaves a window.

		   Only the FIRST page is held. The reorder does a fill of its own and would deadlock
		   against a stub that never answers, which is the test hanging rather than the code,
		   and reads exactly like a mutation that survived. */
		served.page = page(['a', 'b'], 20);
		const cell = new Cell();
		cell.orderBy(null);
		await cell.restart();

		const stuck = held<Record<string, unknown>>();
		let asked = 0;
		vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
			if (path !== '/assets') return { sprite: null };
			asked += 1;
			return asked === 1 ? stuck.promise : page(['x', 'y'], 20);
		});

		const advancing = cell.advance();
		await cell.reorder();
		stuck.release(page(['b', 'c'], 20));
		await advancing;

		expect(cell.playing?.id, 'a stale refill carried a stale advance with it').toBe('a');
	});
});

describe('what the shared filter panel narrows, when it is narrowing a cell', () => {
	/* The seam Theater is a consumer of. `FilterBar` reads and writes a `Narrowing`, and the
	   others behind it are the address and a kept filter's draft. A cell is the one that shows
	   why the shape is right: a wall has four queries at once, so it cannot be the address.
	   This is the cell's own half of it. */

	it('gives back what the panel wrote, in the spelling the panel uses', async () => {
		served.page = page(['a']);
		const cell = new Cell();
		await cell.restart();

		cell.narrowTo(new URLSearchParams({ tags: 'beach' }), { playing: false });

		expect(cell.narrowing.getAll('tags')).toEqual(['beach']);
		expect(cell.source, 'and the cell stores the typed form, which is what it sends').toBe(
			'tags:beach'
		);
	});

	it('drops the named spelling when the source moved by another route', async () => {
		/* TWO FIELDS HOLDING ONE FACT, which is a shape that drifts. The named form is kept
		   beside the typed one so the facet columns can tick; it is believed only while folding
		   it gives back the current source. A wall loaded from a saved layout, or a cell whose
		   source was set any other way, has to fall back to the typed query rather than answer
		   with a filter the cell is not on any more. */
		served.page = page(['a']);
		const cell = new Cell();
		await cell.restart();
		cell.narrowTo(new URLSearchParams({ tags: 'beach' }), { playing: false });

		cell.source = 'tags:mountains';

		expect(cell.narrowing.getAll('tags')).toEqual([]);
		expect(cell.narrowing.get('q')).toBe('tags:mountains');
	});

	it('carries the search mode with the filter rather than beside it', async () => {
		/* A smart search is a different set of files, not a different order over the same ones, so
		   a panel that dropped it would silently widen what the cell draws from. */
		served.page = page(['a']);
		const cell = new Cell();
		await cell.restart();

		cell.narrowTo(new URLSearchParams({ q: 'beach', sort: 'similarity' }), { playing: false });
		expect(cell.sort).toBe('similarity');
		expect(cell.narrowing.get('sort')).toBe('similarity');

		cell.narrowTo(new URLSearchParams({ q: 'beach' }), { playing: false });
		expect(cell.sort).toBeNull();
		expect(cell.narrowing.get('sort')).toBeNull();
	});

	it('has nothing to narrow by before anything has been written, beyond its own order', () => {
		expect([...new Cell().narrowing]).toEqual([['sort', RANDOM]]);
	});

	it('keeps its own order through a filter that names none', async () => {
		/* A kept filter, or a cleared panel, writes the question and no order. Taking that as "in
		   order" would take a shuffled cell off Shuffle every time somebody filtered it. */
		served.page = page(['a']);
		const cell = new Cell();
		await cell.restart();

		cell.narrowTo(new URLSearchParams({ q: 'beach' }), { playing: false });

		expect(cell.sort).toBe(RANDOM);
	});

	it('hands the wall the same filter its own run is fetched with', () => {
		/* The draw and the run have to be filtered identically or the cell opens on something it
		   would then never reach again, so this is `query` itself, less the order and the seed:
		   the draw is a shuffle with a seed of its own. */
		const cell = new Cell();
		cell.source = 'tags:beach';
		const { q, media } = cell.query;

		expect(cell.drawQuery).toEqual({ q, media });
		expect(cell.drawQuery).toEqual({ q: 'tags:beach', media: 'video|gif' });
	});

	it('asks for no draw at all when the cell is searching by MEANING', () => {
		/* A smart search filters to the closest few hundred as well as ordering them, and how far
		   down to reach is sized against a page, which a draw of one has nothing to supply. The
		   cell says so rather than being handed something out of a different set. */
		const cell = new Cell();
		cell.narrowTo(new URLSearchParams({ q: 'beach', sort: 'similarity' }));

		expect(cell.drawQuery).toBeNull();
	});

	it('still asks for a draw on a cell nothing has narrowed', () => {
		expect(new Cell().drawQuery).toEqual({ media: 'video|gif' });
	});

	it('starts the run again from the top, rather than carrying on where it was', async () => {
		/* The whole point of filtering a cell: what it is playing has to come from the new source.
		   Left running, the cell would go on through a list drawn from the filter that was replaced,
		   which reads as the panel having done nothing. */
		served.page = page(['a', 'b', 'c']);
		const cell = new Cell();
		cell.orderBy(null);
		await cell.restart();
		await cell.advance();
		expect(cell.playing?.id).toBe('b');

		served.page = page(['x', 'y']);
		cell.narrowTo(new URLSearchParams({ tags: 'beach' }), { playing: false });
		await vi.waitFor(() => expect(cell.playing?.id).toBe('x'));
	});

	/*
	 * A FILTER CHOSEN OVER A PLAYING CELL WAITS FOR THE FILE TO END.
	 *
	 * The reason is the panel's own shape: a filter is built one facet at a time, and a tick that
	 * threw the run away would make filtering a wall by three columns interrupt three files, the
	 * first of them the one somebody was actually watching.
	 */
	it('holds a narrowing over a playing cell, and shows it as chosen meanwhile', async () => {
		served.page = page(['a', 'b'], 20);
		const cell = new Cell();
		cell.orderBy(null);
		await cell.restart();
		expect(cell.playing?.id).toBe('a');

		served.page = page(['x', 'y'], 20);
		cell.narrowTo(new URLSearchParams({ tags: 'beach' }));

		expect(cell.playing?.id, 'the file somebody was watching was cut off').toBe('a');
		expect(cell.source, 'the run moved to the new filter before the file ended').toBe('');
		expect(cell.narrowingWaits, 'nothing on the cell says a filter is waiting').toBe(true);
		expect(
			cell.narrowing.getAll('tags'),
			'the columns untick themselves in the gesture that ticked them'
		).toEqual(['beach']);
	});

	it('takes the held filter up at the end of the file, whatever the end-behaviour says', async () => {
		/* Even "repeat this", and that is the point of applying it in `ended` rather than in the
		   advance below it: the file nobody wanted interrupted has finished, so a run that has been
		   replaced is not one to repeat. */
		served.page = page(['a', 'b'], 20);
		const cell = new Cell();
		cell.orderBy(null);
		cell.endBehaviour = 'loop_one';
		await cell.restart();
		served.page = page(['x', 'y'], 20);
		cell.narrowTo(new URLSearchParams({ tags: 'beach' }));

		await cell.ended();

		expect(cell.playing?.id).toBe('x');
		expect(cell.source).toBe('tags:beach');
		expect(cell.narrowingWaits).toBe(false);
	});

	it('takes it up on a step forward too, which is the same act', async () => {
		served.page = page(['a', 'b'], 20);
		const cell = new Cell();
		cell.orderBy(null);
		await cell.restart();
		served.page = page(['x', 'y'], 20);
		cell.narrowTo(new URLSearchParams({ tags: 'beach' }));

		await cell.advance();

		expect(cell.playing?.id, 'the step went on through the run that was replaced').toBe('x');
	});

	it('narrows a cell with nothing on screen where it stands', async () => {
		/* There is no end to wait for, so waiting would be a filter that never took effect, which
		   is what an empty cell and a stopped wall both are. */
		served.page = page(['x'], 20);
		const cell = new Cell();

		cell.narrowTo(new URLSearchParams({ tags: 'beach' }));

		expect(cell.narrowingWaits).toBe(false);
		await vi.waitFor(() => expect(cell.playing?.id).toBe('x'));
	});
});

/*
 * THE GAP BETWEEN ONE FILE AND THE NEXT, which is the whole of what a cell does at an `ended`.
 *
 * Nearly all of it is requests: a page of files where the run needs one, and a plan for each
 * candidate. None of that needs the file to have ended, so it is done while it plays.
 */
describe('finding the next file before it is needed', () => {
	it('asks while this one is playing, and spends nothing at the end of it', async () => {
		served.page = page(['a', 'b', 'c']);
		const cell = new Cell();
		cell.orderBy(null);
		await cell.restart();
		expect(cell.playing?.id).toBe('a');

		asked.length = 0;
		// What the view says when the element actually starts. Only a picture that is running has a
		// file's length to spare. See `Cell.started`.
		cell.started();
		await vi.waitFor(() => expect(asked, 'the next file was not looked up early').toEqual(['b']));
		/* And then let the ANSWER land. The ask going out is one microtask and the plan coming back
		   is several more, so an end timed between the two would be measuring the fallback path. */
		await new Promise((resolve) => setTimeout(resolve, 0));

		await cell.ended();

		expect(cell.playing?.id).toBe('b');
		expect(asked, 'the advance asked again for what it was already holding').toEqual(['b']);
	});

	it('does not hand over an answer chosen out of a run that has been replaced', async () => {
		/* The same era check `#fill` carries, and for the same reason: a source changed while the
		   lookahead was out must not have the old source's next file attach itself afterwards. */
		served.page = page(['a', 'b'], 20);
		const cell = new Cell();
		cell.orderBy(null);
		await cell.restart();
		cell.started();
		await vi.waitFor(() => expect(asked).toContain('b'));

		served.page = page(['x', 'y'], 20);
		await cell.restart();
		await cell.advance();

		expect(cell.playing?.id, 'a held file from the old filter was played').toBe('y');
	});

	it('lines up nothing out of a run that was replaced while its next file was being asked about', async () => {
		/* The lookahead's own era check: its answer arrives after the run it came from is gone, and
		   the file it found must not stand lined up as this cell's next. */
		served.page = page(['a', 'b'], 20);
		let release!: (value: unknown) => void;
		const held = new Promise((resolve) => (release = resolve));
		vi.spyOn(api, 'post').mockImplementation(async (path: string) => {
			const id = path.split('/')[2];
			asked.push(id);
			return id === 'b' ? held : plan();
		});
		const cell = new Cell();
		cell.orderBy(null);
		await cell.restart();
		cell.started();
		await vi.waitFor(() => expect(asked).toContain('b'));

		served.page = page(['x', 'y'], 20);
		await cell.restart();
		release(plan());
		await new Promise((done) => setTimeout(done, 0));

		expect(cell.nextUp, 'a file from the old filter was lined up').not.toBe('b');
	});

	it('waits for a search that is still out rather than calling the run over', async () => {
		/* The hole a bare flag would leave. A file can end while the lookahead for it is on the wire (a
		   long clip seeked to its end, or a slow answer), and the advance would then find an empty
		   run, be refused a refill because a fill for the same era was already out, and say the
		   source had nothing left: *Stopped at the end*, in the middle of a library. */
		served.page = page(['b', 'c'], 20);
		const cell = new Cell();
		cell.orderBy(null);
		// Opened on one particular file, which is how a wall arrives: the run behind it is empty, so
		// the lookahead's first act is to fetch a page.
		await cell.startOn(file('opened'));
		let release!: (value: Record<string, unknown>) => void;
		const stuck = new Promise<Record<string, unknown>>((resolve) => (release = resolve));
		vi.spyOn(api, 'get').mockImplementation(async (path: string) =>
			path === '/assets' ? stuck : { sprite: null }
		);

		cell.started();
		const ending = cell.ended();
		release(page(['b', 'c'], 20));
		await ending;

		expect(cell.state, 'the cell stopped while its own search was still out').not.toBe('stopped');
		expect(cell.playing?.id).toBe('b');
	});

	it('finds it for itself when nothing was found ahead', async () => {
		// Nothing arms the lookahead here (the element never said it was playing), so the advance
		// does the work where it stands. A cell must never depend on having been looked ahead for.
		served.page = page(['a', 'b']);
		const cell = new Cell();
		cell.orderBy(null);
		await cell.restart();

		await cell.advance();

		expect(cell.playing?.id).toBe('b');
	});
});

/*
 * HOW LOUD IS THE APPLICATION'S, not this cell's. See `$lib/player/loudness`.
 *
 * Nine cells, the player and the panel in the corner each keeping a copy of one preference would
 * mean a level set on any of them reached none of the others. What a cell owns is whether it is
 * the one being heard.
 */
describe('how loud a cell is', () => {
	it('follows the one level every other picture in the window is following', () => {
		const one = new Cell();
		const other = new Cell();

		one.volume = 40;

		expect(other.volume, 'two cells disagreed about how loud Sift is').toBe(40);
		expect(loudness.level).toBe(40);

		loudness.set(70);
		expect(one.volume).toBe(70);
	});

	it('is not taken from a saved wall, which would be a second authority over it', () => {
		loudness.set(55);
		const cell = new Cell();

		cell.adopt({ ...new Cell().saved, volume: 10 });

		expect(cell.volume, 'a wall saved last week moved how loud Sift is today').toBe(55);
		expect(cell.saved.volume, 'and a saved cell still carries the level it was heard at').toBe(55);
	});
});

describe('what a cell keeps of the file it is playing', () => {
	/*
	 * A CELL KEEPS ENOUGH FOR A VERB TO WORD ITSELF.
	 *
	 * A cell holding only a `Playable` carries neither the heart nor the stars, and fabricating
	 * them would make the heart a control that LIES: it would read Favorites over a file that is
	 * already one. But a cell fills its run straight from `GET /assets`, and an `AssetSummary`
	 * carries all three, so the type has to keep them rather than throw them away.
	 *
	 * Read out of the source, because what is under test is a TYPE: there is nothing at run time
	 * to strip a field off an object, which is exactly why dropping one goes unnoticed. The three
	 * are `Actionable`'s: the least a row has to be for a verb to act on it
	 * (`lib/grid/actions.svelte`).
	 */
	it('names the heart, the stars and the vault verdict among the fields it picks', () => {
		const source = readFileSync(resolve('src/lib/theater/cell.svelte.ts'), 'utf8');
		const picked = source.slice(
			source.indexOf('export type Playable'),
			source.indexOf('>;', source.indexOf('export type Playable'))
		);

		expect(picked).toContain("'favorite'");
		expect(picked).toContain("'rating'");
		expect(picked).toContain("'concealed'");
	});

	it('hands the file it is playing to the verbs rather than an empty list', () => {
		// Not `[]`, which would make the heart unwordable. The host looks a file up in what the
		// surface is showing, and for a cell the file on screen IS what it is showing.
		const menu = readFileSync(resolve('src/lib/components/theater/CellMenu.svelte'), 'utf8');

		expect(menu).toContain('items={showing}');
		expect(menu, 'an empty list cannot word a heart').not.toContain('items={[]}');
	});
});
