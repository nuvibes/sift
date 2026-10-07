/* The few things that are true of a whole wall: its shape, and its sound.
 *
 * The sound is the half worth testing hardest, and not because it is complicated. Everything here
 * is one line of logic, and every one of them is a line whose wrong answer is invisible: a cell
 * that is quiet when it should not be looks exactly like a cell somebody muted, and focus and
 * audio drifting apart is the single most repeated complaint about every product in this shape.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { MOST_CELLS, writeShape, type Shape } from './layouts';
import { Cell, type Playable } from './cell.svelte';
import { showing, Wall } from './wall.svelte';
import { vault } from '$lib/shell/vault.svelte';
import { session, type Viewer } from '$lib/shell/session.svelte';

/* A saved custom wall of six, two rows of three: past the top of the ladder, so the one kind of wall
   a removal still leaves with a hole to heal. */
const SIX: Shape = {
	rows: 2,
	cols: 3,
	slots: [0, 1, 2, 3, 4, 5].map((at) => ({
		row: Math.floor(at / 3),
		col: at % 3,
		rowSpan: 1,
		colSpan: 1
	}))
};

beforeEach(() => {
	// Nothing here is about what a cell plays, so every source is empty and no plan is ever asked
	// for. What is under test is the wall around them.
	vi.spyOn(api, 'get').mockImplementation(async () => ({ items: [], total: 0 }));
	// A wall reports its session as it goes; what the server makes of that is not under test here.
	vi.spyOn(api, 'post').mockImplementation(async () => undefined);
});

describe('what a wall opens on', () => {
	/*
	 * A cell's volume is the PLAYER's volume, and it is set while the cell is still muted.
	 *
	 * A cell starting at 100 regardless of the setting would unmute at full volume every time,
	 * from an application that had been told, in its own settings, how loud to play things. The
	 * number has to be right BEFORE the unmute, because it is what the sound comes back at; a value
	 * that arrived when somebody unmuted would arrive one loud second too late.
	 */
	it('takes the volume the ordinary player is set to', async () => {
		vi.spyOn(api, 'get').mockImplementation(async (path: string) =>
			path === '/settings'
				? { sections: [{ settings: [{ key: 'playback.volume', value: 35 }] }] }
				: { items: [], total: 0 }
		);
		const wall = new Wall();

		await wall.open();

		expect(wall.cells[0].volume, 'a cell opened at full volume regardless of the setting').toBe(35);
		expect(wall.cells[0].muted, 'a cell must still open silent').toBe(true);
	});

	/* `Settings > Playback`, the Theater rows: each is read when the wall opens, and each turned away
	 * from its default here, so a row the wall stopped reading would leave the default standing. */
	it('opens running, advancing and keeping itself as the Theater rows say', async () => {
		vi.spyOn(api, 'get').mockImplementation(async (path: string) =>
			path === '/settings'
				? {
						sections: [
							{
								settings: [
									{ key: 'theater.autoplay', value: true },
									{ key: 'theater.center_stage', value: 'newest' },
									{ key: 'theater.timer_seconds', value: 30 },
									{ key: 'theater.resume', value: true }
								]
							}
						]
					}
				: { items: [], total: 0 }
		);
		const wall = new Wall();

		await wall.open();

		expect(wall.paused, 'autoplay on, and the wall opened held').toBe(false);
		expect(wall.newestTakesFocus).toBe(true);
		expect(wall.cells.map((cell) => cell.timerSeconds)).toEqual(wall.cells.map(() => 30));
		expect(wall.resumes).toBe(true);
	});

	/*
	 * EACH CELL OPENS SOMEWHERE ELSE IN THE LIBRARY, not on the first page of it.
	 *
	 * A cell fills its run from `/assets` at offset 0 and shuffles the sixty rows it gets back, so
	 * without a draw a wall arriving from nothing would pick every cell out of the newest sixty
	 * files and shuffle them among themselves, which looks random and is the same sixty files
	 * every evening. What is held here is the pair a reader could not check: that the draw is made
	 * at all, and that two cells never land on the same file.
	 */
	describe('the opening draw', () => {
		/** One tile, as a page of the library answers with. */
		function file(id: string, media_type = 'video') {
			return {
				id,
				media_type,
				duration_ms: 1000,
				thumb: true,
				art: null,
				original_filename: null,
				width: 1920,
				height: 1080
			};
		}

		/**
		 * The server, with the pages it will hand back to the DRAWS in the order they are made.
		 *
		 * A draw and a cell filling its own run are the same route now (the draw is a page of a
		 * freshly seeded shuffle), so the two are told apart here the way the server tells them
		 * apart: by whether the ask carries `sort=random`. That is deliberate rather than
		 * convenient, because it is exactly the property the wall has to get right.
		 */
		function serving(draws: unknown[][]): {
			pages: number;
			asked: number;
			asks: Record<string, string | undefined>[];
		} {
			const counted = { pages: 0, asked: 0, asks: [] as Record<string, string | undefined>[] };
			let at = 0;
			vi.spyOn(api, 'get').mockImplementation(async (path: string, options?: unknown) => {
				const query =
					((options ?? {}) as { query?: Record<string, string | undefined> }).query ?? {};
				// A draw is sized to the wall; a cell filling its own shuffled run asks for a page.
				if (path === '/assets' && query.sort === 'random' && query.limit !== '60') {
					counted.asked += 1;
					counted.asks.push(query);
					const answer = draws[at] ?? [];
					at += 1;
					return { items: answer, total: answer.length };
				}
				if (path === '/assets') counted.pages += 1;
				return { items: [], total: 0 };
			});
			// Whatever is drawn can be played, so the cell gets as far as showing it.
			vi.spyOn(api, 'post').mockImplementation(async () => ({
				route: 'direct',
				streamable: true,
				url: '/stream',
				reason: '',
				scale_height: null,
				resume_ms: null
			}));
			return counted;
		}

		it('opens every cell from ONE request', async () => {
			/* The whole point: every cell of a wall arriving from nothing is on the same filter,
			   so one page of as many rows as there are cells fills all of them. A draw per cell,
			   each naming the last, would be serial by construction: a round trip per cell
			   before the first picture. */
			const served = serving([[file('a'), file('b'), file('c')]]);
			const wall = new Wall();

			await wall.open();

			expect(wall.cells.map((cell) => cell.playing?.id)).toEqual(['a', 'b', 'c']);
			expect(served.asked, 'the wall asked more than once for one filter').toBe(1);
			expect(served.asks[0].limit, 'the ask was not sized to the wall').toBe('3');
			expect(served.pages, 'a cell filled itself from the first page instead of drawing').toBe(0);
		});

		it('gives the draw a deadline, so a wall cannot open onto an ask that never answers', async () => {
			/* The same fifteen seconds a cell's own page and every plan have: without a
			   deadline, a wall opened while the browser's six connections are held by streams
			   would sit on "Finding something to play" for as long as the ask was queued. */
			const signals: unknown[] = [];
			serving([[file('a'), file('b'), file('c')]]);
			const answer = vi.mocked(api.get).getMockImplementation();
			vi.spyOn(api, 'get').mockImplementation(async (path: string, options?: unknown) => {
				const asked = (options ?? {}) as { query?: { sort?: string }; signal?: unknown };
				if (path === '/assets' && asked.query?.sort === 'random') signals.push(asked.signal);
				return answer?.(path as never, options as never);
			});
			const wall = new Wall();

			await wall.open();

			expect(signals, 'the opening draw was not made').toHaveLength(1);
			expect(signals[0], 'the draw was asked for with no deadline').toBeInstanceOf(AbortSignal);
		});

		it('asks for a shuffle of its own each time, rather than the day the server falls back to', async () => {
			/* Without a seed the server gives an unseeded shuffle the day's own offset, which holds
			   for as long as the visit does, and the same files every evening, looking shuffled, is
			   the exact fault the opening draw exists to fix. */
			const seeds = new Set<string | undefined>();
			for (let round = 0; round < 6; round += 1) {
				const served = serving([[file('a'), file('b')]]);
				const wall = new Wall();
				await wall.open();
				seeds.add(served.asks[0]?.seed);
			}
			expect(seeds.has(undefined), 'a draw was made with no seed at all').toBe(false);
			expect(seeds.size, 'every opening asked for the same shuffle').toBeGreaterThan(1);
		});

		it('takes the rows of the one page by position, so no two cells open on the same file', async () => {
			/* How distinctness is had: a page cannot hold the same row twice, which is stronger
			   than an `avoiding` per draw, which only ever avoids the draw immediately before
			   it. */
			const served = serving([[file('a'), file('b'), file('c')]]);
			const wall = new Wall();

			await wall.open();

			expect(wall.cells.map((cell) => cell.playing?.id)).toEqual(['a', 'b', 'c']);
			expect(served.asks[0].sort).toBe('random');
		});

		it("draws under the cells' own filter rather than throwing a draw back", async () => {
			/* A cell set to Video and GIF does not draw photographs. The filtering travels with
			   the ask, rather than an unfiltered draw discarded afterwards, which on a library
			   that is mostly photographs would waste every draw a cell made. */
			const served = serving([[file('a'), file('b')]]);
			const wall = new Wall();

			await wall.open();

			expect(served.asks.map((one) => one.media)).toEqual(['video|gif']);
		});

		it('gives a narrowed cell a group, and an ask, of its own', async () => {
			/* Two filters is two questions and there is no honest way to ask them as one. They still
			   go out together, so the wall waits once rather than twice. */
			const served = serving([[file('a'), file('c')], [file('b')]]);
			const wall = new Wall();
			wall.all[1].source = 'people:Ada Lumen';

			await wall.open();

			expect(wall.cells.map((cell) => cell.playing?.id)).toEqual(['a', 'b', 'c']);
			expect(served.asked, 'two filters were asked as one question').toBe(2);
			expect(new Set(served.asks.map((one) => one.q))).toEqual(
				new Set([undefined, 'people:Ada Lumen'])
			);
			expect(served.pages, 'a narrowed cell filled itself instead of drawing').toBe(0);
		});

		it('leaves a cell searching by MEANING to find its own', async () => {
			/* The one filter the route cannot be given: a smart search filters to the closest few
			   hundred as well as ordering them, and how far down to reach is sized against a page,
			   which a draw has nothing to supply. */
			const served = serving([[file('a'), file('c')]]);
			const wall = new Wall();
			wall.all[1].source = 'beach';
			wall.all[1].sort = 'similarity';

			await wall.open();

			expect(wall.cells[0].playing?.id).toBe('a');
			expect(wall.cells[1].playing, 'a draw landed in a cell the route cannot narrow').toBeNull();
			expect(served.pages, 'the smart cell did not fill itself').toBe(1);
			expect(served.asked, 'the smart cell was asked for a draw anyway').toBe(1);
		});

		it('fills the cells the ordinary way when there is nothing to draw', async () => {
			/* An empty library, or one with nothing this account may see. One ask for the filter they
			   share, and then every cell it could not answer for fills itself. */
			const served = serving([]);
			const wall = new Wall();

			await wall.open();

			expect(served.pages).toBe(3);
			expect(served.asked, 'the wall asked more than once for one filter').toBe(1);
		});

		/*
		 * WHILE THE WALL IS OPENING, A CELL IS NOT "NOTHING CHOSEN YET".
		 *
		 * Both round trips are inside this (the account's preferences and the draw), because the
		 * sentence somebody reads on a page-load is worn through both of them.
		 */
		it('says it is opening until every cell has been answered for', async () => {
			serving([[file('a'), file('b')]]);
			const wall = new Wall();
			expect(wall.opening, 'a wall nobody has opened claims to be opening').toBe(false);

			const opening = wall.open();
			expect(wall.opening, 'the cells were left saying nothing was chosen').toBe(true);

			await opening;
			expect(wall.opening, 'the wall never stopped saying it was opening').toBe(false);
		});
	});
});

describe('two cells and one file', () => {
	it('is avoided: a cell steps over the file another cell is showing', async () => {
		const tile = (id: string) => ({
			id,
			media_type: 'video',
			duration_ms: 1000,
			thumb: true,
			art: null,
			original_filename: null,
			width: 1920,
			height: 1080
		});
		vi.spyOn(api, 'get').mockImplementation(async (path: string) =>
			path === '/assets' ? { items: [tile('a'), tile('b')], total: 2 } : { sprite: null }
		);
		vi.spyOn(api, 'post').mockImplementation(async () => ({
			route: 'direct',
			streamable: true,
			url: '/stream',
			reason: '',
			scale_height: null,
			resume_ms: null
		}));
		const wall = new Wall();

		await wall.cells[0].restart();
		await wall.cells[1].restart();

		expect(wall.cells[0].playing?.id).toBe('a');
		expect(wall.cells[1].playing?.id, 'two cells showed one file').toBe('b');
	});
});

describe('the shape', () => {
	it('draws as many cells as the shape has places', () => {
		const wall = new Wall();
		// A wall opens on three feeds side by side.
		expect(wall.cells).toHaveLength(3);

		wall.setLayout('grid');
		expect(wall.cells).toHaveLength(4);

		wall.setLayout('side_by_side');
		expect(wall.cells).toHaveLength(2);
	});

	it('keeps a cell that survives a change of shape', () => {
		const wall = new Wall();
		wall.setLayout('grid');
		wall.all[1].source = 'people:Jane';

		wall.setLayout('side_by_side');

		expect(wall.cells[1].source, 'a source somebody set up was thrown away by a reshape').toBe(
			'people:Jane'
		);
	});

	it('lets go of a cell that falls off the end', () => {
		const wall = new Wall();
		wall.setLayout('grid');
		wall.all[3].state = 'ready';

		wall.setLayout('side_by_side');

		expect(wall.all[3].state, 'a cell nobody can see was left holding its run').toBe('empty');
	});

	it('brings the keyboard and the sound back inside the shape', () => {
		const wall = new Wall();
		wall.setLayout('grid');
		wall.promote(3);

		wall.setLayout('side_by_side');

		expect(wall.focused).toBeLessThan(2);
		expect(wall.audible).toBeNull();
	});
});

describe('Center stage', () => {
	/* The arrangement itself: a focus half that is an ordinary wall, and a strip that is not part of
	   its grid. Everything below is about the seam between the two. */
	it('opens as one feed in focus with five previews under it', () => {
		const wall = new Wall();

		wall.setLayout('center_stage');

		expect(wall.inFocus, 'the focus half is the shape').toHaveLength(1);
		expect(wall.previews, 'the strip is the number beside it').toHaveLength(5);
		expect(wall.cells, 'and the cells are one list of both').toHaveLength(6);
		expect(wall.centerStage).toBe(true);
		expect(wall.layout, 'stored under its own name, not as a wall of one').toBe('center_stage');
	});

	/* The fault this guards is silent: `reshape` releases every cell past the last drawn one, and
	   counting the shape's places alone would release all five previews the moment the focus half
	   changed shape, with the strip still on screen, drawing cells that had been let go. */
	/* The fault this guards is silent: `reshape` releases every cell past the last drawn one, and
	   counting the shape's places alone would release all five previews the moment the focus half
	   changed shape, with the strip still on screen, drawing cells that had been let go. */
	it('keeps the previews when the focus half changes shape', () => {
		const wall = new Wall();
		wall.setLayout('center_stage');
		wall.previews[0].source = 'cats';
		/* And a state that is not `empty`, because that is what being RELEASED looks like: a source
		   survives being let go and the run does not, so asserting on the words alone cannot see the
		   fault this test is here for. */
		wall.previews.forEach((cell) => (cell.state = 'ready'));

		wall.setLayout('center_stage_grid');

		expect(wall.inFocus).toHaveLength(4);
		expect(wall.previews, 'the strip belongs to the wall, not to the shape').toHaveLength(5);
		expect(wall.previews[0].source, 'and a preview keeps what was set on it').toBe('cats');
		expect(
			wall.previews.map((cell) => cell.state),
			'and its run: a released preview is one that starts again from nothing'
		).toEqual(['ready', 'ready', 'ready', 'ready', 'ready']);
	});

	/* And the other half of that rule: a preset with NO strip takes the strip away. The four
	   Center stage presets are what grow the focus half with previews up, so leaving a strip
	   alone would only contradict the picker: choosing Grid would leave five previews under a
	   wall whose picture showed four plain squares. */
	it('closes the strip when a layout with none is chosen', () => {
		const wall = new Wall();
		wall.setLayout('center_stage_grid');
		expect(wall.previews).toHaveLength(5);

		wall.setLayout('grid');

		expect(wall.previews, 'the picture showed four squares and no strip').toHaveLength(0);
		expect(wall.cells).toHaveLength(4);
	});

	it('brings a preview up into the place the keyboard is on, and sends that one down', () => {
		const wall = new Wall();
		wall.setLayout('center_stage');
		wall.setLayout('side_by_side');
		wall.addPreview();
		wall.inFocus[1].source = 'was in focus';
		const preview = wall.inFocus.length;
		wall.previews[0].source = 'was waiting';
		wall.focus(1);

		wall.sendToFocus(preview);

		expect(wall.inFocus[1].source, 'the preview came up where the keyboard was').toBe(
			'was waiting'
		);
		expect(wall.previews[0].source, 'and what was there went down in its place').toBe(
			'was in focus'
		);
		expect(wall.focused, 'the keyboard stays on the place, not on the cell').toBe(1);
	});

	/* A press on a preview chooses it, so the keyboard is on the strip when its double press
	   brings it up: it still lands on the place last in focus. */
	it('lands on the place last in focus when the preview itself was chosen first', () => {
		const wall = new Wall();
		wall.setLayout('center_stage');
		wall.setLayout('side_by_side');
		wall.addPreview();
		wall.inFocus[1].source = 'was in focus';
		const preview = wall.inFocus.length;
		wall.previews[0].source = 'was waiting';
		wall.focus(1);
		wall.focus(preview);

		wall.sendToFocus(preview);

		expect(wall.inFocus[1].source, 'the preview landed on the first place').toBe('was waiting');
		expect(wall.focused).toBe(1);
	});

	/*
	 * THE CLIP IN THE PICTURE, not another one out of the same source.
	 *
	 * Handing the focus cell the preview's SOURCE would throw the run away and pick something
	 * fresh, so pressing a preview would put a different clip on the wall than the one that had just
	 * been pressed.
	 */
	it('brings up the file the preview had, not a fresh one from its source', () => {
		const wall = new Wall();
		wall.setLayout('center_stage');
		const preview = wall.previews[0];
		preview.source = 'cats';
		const pressed = { id: 'the-one-in-the-picture' } as unknown as Playable;
		preview.playing = pressed;
		preview.position = 12.5;
		const taking = vi.spyOn(wall.inFocus[0], 'takeOver').mockResolvedValue();

		wall.sendToFocus(wall.inFocus.length);

		// The source, the file in the picture, and where that file had reached: all three, because
		// the swap builds both elements again and a clip told none of them starts from the top.
		expect(taking).toHaveBeenCalledWith(expect.objectContaining({ source: 'cats' }), pressed, 12.5);
	});

	it('refuses to promote a cell that is not in the strip', () => {
		const wall = new Wall();
		// TWO in focus and a different one chosen, so a promotion that went ahead would be visible.
		// Asking it to promote the cell the keyboard is already on cannot fail: swapping a cell with
		// itself is what a wall looks like when nothing happened.
		wall.setLayout('side_by_side');
		wall.inFocus[0].source = 'first';
		wall.inFocus[1].source = 'second';
		wall.focus(0);

		wall.sendToFocus(1);

		expect(
			wall.inFocus.map((cell) => cell.source),
			'a feed in focus is already in focus; there is nothing to bring up'
		).toEqual(['first', 'second']);
	});

	/* A saved wall has to come back as the wall that was saved. The strip is stored beside the
	   shape rather than inside it, so this is the one place the two halves are put back together. */
	it('keeps the strip when a wall is saved and loaded again', () => {
		const wall = new Wall();
		wall.setLayout('center_stage');
		wall.inFocus[0].source = 'in focus';
		wall.previews[0].source = 'first preview';
		wall.previews[4].source = 'last preview';

		const stored = wall.preset;
		const opened = new Wall();
		opened.adopt(stored);

		expect(stored.strip, 'the strip is written down').toBe(5);
		expect(opened.previews, 'and read back').toHaveLength(5);
		expect(opened.inFocus[0].source).toBe('in focus');
		expect(opened.previews[0].source).toBe('first preview');
		expect(opened.previews[4].source, 'in the order they were in').toBe('last preview');
	});

	/* Four in focus once there is a strip, nine without one. Four is where a feed stops being big
	   enough to be the thing you are watching, which is true whether the strip is full or not. */
	it('caps the focus half at four once there is a strip', () => {
		const wall = new Wall();
		wall.setLayout('center_stage_grid');
		expect(wall.inFocus, 'four up').toHaveLength(4);
		expect(wall.canGrow, 'and no more while a strip is under them').toBe(false);

		/* WITH A CELL TO SPARE, which is the only arrangement that asks the question.

		   Four in focus and five waiting is nine, and nine is the wall, so the wall's OWN ceiling
		   says no there whether the four is a cap or not, and a test that stops at the line above
		   passes with the cap taken out. One preview short, eight cells are drawn and there is room
		   in the list: the only thing that can refuse a fifth feed then is the four. */
		wall.dropPreview(wall.inFocus.length);
		expect(wall.previews, 'eight cells drawn, one spare').toHaveLength(4);
		expect(wall.canGrow, 'and the four is what refuses, not the nine').toBe(false);

		wall.closeStrip();
		expect(wall.canGrow, 'without one, nine is the ceiling').toBe(true);
	});

	/* Nine cells in all, however they are divided. A strip capped at five as well would stop a
	   wall of three in focus at eight while a wall of four reached nine: the same nine, and only
	   one arrangement able to use them all. Five is what Center stage OPENS with. */
	it('lets the strip take every cell the focus half is not using', () => {
		const wall = new Wall();
		wall.setLayout('center_stage_three');
		expect(wall.cells, 'three up and five waiting').toHaveLength(8);
		expect(wall.canPreview, 'and one cell of the nine still spare').toBe(true);

		wall.addPreview();
		expect(wall.previews).toHaveLength(6);
		expect(wall.canPreview, 'and now there is not').toBe(false);

		wall.setLayout('grid');
		for (let n = 0; n < 9; n += 1) wall.addPreview();
		expect(wall.previews.length + wall.inFocus.length, 'nine is the wall').toBe(MOST_CELLS);
		expect(wall.canPreview).toBe(false);
	});

	/*
	 * SENDING A FEED DOWN.
	 *
	 * It COPIES rather than moves: the reasoning is beside `sendToStrip`, and it is what this
	 * asserts: the cell that was pressed still draws from what it drew from, and the preview draws
	 * from the same thing. A move would leave a hole in the shape, and closing a hole means
	 * reshaping the wall under somebody who only asked for a preview.
	 */
	it('copies a feed down into the strip and leaves the cell where it was', () => {
		const wall = new Wall();
		wall.setLayout('center_stage');
		const before = wall.previews.length;
		wall.at(0)!.source = 'harbour lights';

		wall.sendToStrip(0);

		expect(wall.previews, 'one more waiting').toHaveLength(before + 1);
		expect(wall.previews[0].source, 'drawing from what was sent down').toBe('harbour lights');
		expect(wall.at(0)?.source, 'and the cell carries on').toBe('harbour lights');
	});

	it('refuses to send a preview to the strip it is already in', () => {
		// Otherwise the row would copy a preview beside itself, which is Duplicate and is offered on
		// the wall's own cells. A preview's menu offers Bring this up instead.
		const wall = new Wall();
		wall.setLayout('center_stage');
		const at = wall.inFocus.length;
		const before = wall.previews.length;

		wall.sendToStrip(at);

		expect(wall.previews).toHaveLength(before);
	});

	/* Dropped from the MIDDLE of the strip, which is the only place the slide can be seen.

	   The strip is the end of the list of nine and it shortens from the FRONT, so dropping the first
	   preview needs no slide at all: the cell simply stops being drawn, and a test that drops that
	   one passes with the slide deleted. Dropping the second means every source in front of it has
	   to move along by one, and what proves it moved is the one BEFORE it still being there. */
	it('slides the sources up when a preview is dropped', () => {
		const wall = new Wall();
		wall.setLayout('center_stage');
		wall.previews[0].source = 'first';
		wall.previews[1].source = 'second';
		wall.previews[2].source = 'third';
		const at = wall.inFocus.length;

		wall.dropPreview(at + 1);

		expect(wall.previews).toHaveLength(4);
		expect(wall.previews[0].source, 'the one in front of it moved along').toBe('first');
		expect(wall.previews[1].source, 'and the one after it closed the gap').toBe('third');
	});

	/* A strip read off a stored wall is CLAMPED rather than trusted. The row is written by whatever
	   version saved it, and a number bigger than the list can hold would draw previews out of cells
	   that are not there: `previews` would be a slice starting before the shape's own places, so
	   the same cell is both a feed and a preview. */
	it('refuses a stored strip too big for the wall it is on', () => {
		const wall = new Wall();
		wall.setLayout('grid');
		const stored = { ...wall.preset, strip: 99 };

		const opened = new Wall();
		opened.adopt(stored);

		expect(opened.previews, 'whatever the focus half is not using, and no more').toHaveLength(
			MOST_CELLS - opened.inFocus.length
		);
		expect(opened.cells, 'nine is the wall').toHaveLength(MOST_CELLS);
	});

	/* The two modes, and the loop between them.

	   Promoting sends the feed that was in focus DOWN into the strip, where it starts its next file a
	   moment later, and a start in the strip is exactly what `newest` promotes on. Without the
	   guard, one promotion is a wall that swaps back and forth for ever. */
	it('promotes a preview that starts something new, but only in that mode', () => {
		const wall = new Wall();
		wall.setLayout('center_stage');
		const at = wall.inFocus.length;
		wall.previews[0].source = 'the newest';

		wall.previewStarted(at, 'a-file');
		expect(wall.inFocus[0].source, 'pick is the default and it waits to be asked').not.toBe(
			'the newest'
		);

		wall.newestTakesFocus = true;
		wall.previewStarted(at, 'another-file');
		expect(wall.inFocus[0].source, 'newest brings it up on its own').toBe('the newest');
	});

	/*
	 * A START IS NOT A NEW FILE.
	 *
	 * An element announces a start for a seek, for coming off a pause and after a stall. Promoting
	 * on all of them would mean that choosing a preview from the bar and pressing back-5 took it
	 * into focus, so one press moved the controls off the very cell being driven.
	 */
	it('does not promote a preview that has merely started the same file again', () => {
		const wall = new Wall();
		wall.setLayout('center_stage');
		wall.newestTakesFocus = true;
		const at = wall.inFocus.length;
		wall.previews[0].source = 'waiting';

		// It reaches a file, which is news, and comes up.
		wall.previewStarted(at, 'clip-one');
		expect(wall.inFocus[0].source, 'the first start is a new file').toBe('waiting');

		/* The cell that went DOWN announces the file it landed on, which the settling guard eats.
		   See the test below. This one is that announcement, and it is not what is under test here. */
		wall.previewStarted(at, 'clip-one');

		// NOW the same cell announces the same file again: a seek, or coming off a pause. A mode
		// reading every start as news would promote here, and the selection would jump off
		// whatever the bar was driving.
		wall.inFocus[0].source = 'settled';
		wall.previews[0].source = 'still waiting';
		wall.previewStarted(at, 'clip-one');

		expect(wall.inFocus[0].source, 'and nothing moves').toBe('settled');
	});

	it('does not promote the feed it has just sent down into the strip', () => {
		const wall = new Wall();
		wall.setLayout('center_stage');
		wall.newestTakesFocus = true;
		wall.inFocus[0].source = 'was in focus';
		const at = wall.inFocus.length;
		wall.previews[0].source = 'came up';

		wall.sendToFocus(at);
		// The cell that went down starts its next file, which is a start in the strip.
		wall.previewStarted(at, 'whatever-it-found');

		expect(wall.inFocus[0].source, 'the wall settles instead of swapping back').toBe('came up');
	});
});

/** Which cells of a wall are heard, by position. */
function heardCells(wall: Wall): number[] {
	return wall.cells.flatMap((_, at) => (wall.isAudible(at) ? [at] : []));
}

describe('the sound', () => {
	it('starts silent, because no browser grants a page sound it was not asked for', () => {
		const wall = new Wall();
		expect(wall.masterMuted).toBe(true);
		expect(wall.all.every((cell) => cell.muted)).toBe(true);
		expect(wall.isAudible(0)).toBe(false);
	});

	it('is silent while EITHER answer says so', () => {
		const wall = new Wall();
		wall.toggleMute(0);
		expect(wall.isAudible(0)).toBe(true);

		wall.toggleMaster();
		expect(wall.isAudible(0), 'a cell was audible through the master mute').toBe(false);
	});

	it('lets several cells be heard together, which is the point of the wall', () => {
		const wall = new Wall();
		wall.setLayout('grid');
		wall.toggleMute(0);
		wall.toggleMute(2);

		expect(wall.isAudible(0)).toBe(true);
		expect(wall.isAudible(2)).toBe(true);
	});

	it('silences everything else when one cell is soloed', () => {
		const wall = new Wall();
		wall.setLayout('grid');
		wall.toggleMute(0);
		wall.toggleMute(1);

		wall.solo(2);

		expect(wall.isAudible(2)).toBe(true);
		expect(wall.isAudible(0)).toBe(false);
		expect(wall.isAudible(1)).toBe(false);
	});

	it('takes the sound with the cell that is promoted', () => {
		/* Focus and audio desyncing is the one complaint every product in this shape gets. */
		const wall = new Wall();
		wall.setLayout('grid');
		wall.solo(0);

		wall.promote(2);

		expect(wall.focused).toBe(2);
		expect(wall.isAudible(2)).toBe(true);
		expect(wall.isAudible(0), 'the sound stayed where the focus had left').toBe(false);
	});

	it('points at another cell that is still audible when one is muted', () => {
		const wall = new Wall();
		wall.setLayout('grid');
		wall.toggleMute(0);
		wall.toggleMute(1);

		wall.toggleMute(1);

		expect(wall.audible).toBe(0);
	});

	it('points at nobody once the last audible cell is muted', () => {
		const wall = new Wall();
		wall.toggleMute(0);

		wall.toggleMute(0);

		expect(wall.audible).toBeNull();
	});

	it('names nobody to the operating system while everything is silenced', () => {
		/* `audible` is where the sound last MOVED to and stays put through a master mute: that is
		 * what lets it come back to the same cell. What is OUTSIDE the wall has to ask a different
		 * question: an entry on a lock screen naming a clip while the whole wall is muted is the
		 * operating system being told something untrue. */
		const wall = new Wall();
		wall.toggleMute(0);
		expect(wall.heard).toBe(0);

		wall.toggleMaster();

		expect(wall.audible, 'the sound forgot which cell to come back to').toBe(0);
		expect(wall.heard, 'a silenced wall still named a clip').toBeNull();
	});

	it('names it again when the sound comes back', () => {
		const wall = new Wall();
		wall.toggleMute(0);
		wall.toggleMaster();

		wall.toggleMaster();

		expect(wall.heard).toBe(0);
	});

	it('notes when the sound moved, so the marks can fade', () => {
		const wall = new Wall();
		expect(wall.soundMovedAt).toBe(0);

		wall.toggleMute(0);

		expect(wall.soundMovedAt).toBeGreaterThan(0);
	});

	it('moving the keyboard alone does not move the sound', () => {
		const wall = new Wall();
		wall.setLayout('grid');
		wall.solo(0);

		wall.focus(2);

		expect(wall.focused).toBe(2);
		expect(wall.isAudible(0), 'arrowing between cells changed what was playing aloud').toBe(true);
	});

	it('lets back only the chosen cell when its Unmute is pressed on a silenced wall', () => {
		// Sound on, then Silence everything: every cell's own sound is on under the wall's silence.
		const wall = new Wall();
		wall.setLayout('grid');
		wall.toggleMaster();
		wall.toggleMaster();
		expect(heardCells(wall)).toEqual([]);

		wall.focus(2);
		wall.setMuted(wall.addressedAt, false);

		expect(heardCells(wall), 'one Unmute let the whole wall back').toEqual([2]);
	});

	it('mutes only the chosen cell when its Mute is pressed', () => {
		const wall = new Wall();
		wall.setLayout('grid');
		wall.toggleMaster();

		wall.focus(1);
		wall.setMuted(wall.addressedAt, true);

		expect(heardCells(wall)).toEqual([0, 2, 3]);
	});

	it('hears only the cell asked for, by its place on the wall, when the wall has a strip', () => {
		const wall = new Wall();
		wall.setLayout('center_stage');
		expect(wall.strip).toBeGreaterThan(0);

		wall.solo(3);

		expect(heardCells(wall)).toEqual([3]);
	});
});

describe('arriving', () => {
	it('leaves everything muted whatever the account prefers', async () => {
		/* The one thing on this screen that cannot be restored from a preference. Both engines pause
		 * an element that is unmuted without somebody having interacted with the page, so a wall that
		 * remembered its sound would open and immediately stop, which reads as playback failing
		 * rather than as an unmute being refused. */
		const wall = new Wall();

		await wall.open();

		expect(wall.masterMuted, 'a wall unmuted itself on arrival').toBe(true);
		expect(wall.all.every((cell) => cell.muted)).toBe(true);
		expect(wall.audible).toBeNull();
	});
});

describe('the vault shutting', () => {
	it('empties every drawn cell before it fills again', () => {
		const wall = new Wall();
		wall.setLayout('grid');
		for (const cell of wall.cells) cell.state = 'ready';

		wall.vaultChanged(true);

		// `restart` sets its own state as it goes; what matters is that nothing was left holding the
		// picture it had. Every cell has let go of what it was showing.
		expect(wall.cells.every((cell) => cell.playing === null)).toBe(true);
	});

	it('leaves what is on screen alone when the vault OPENS', () => {
		/* There is nothing to take away: what changed is that there is more to draw from. */
		const wall = new Wall();
		const before = wall.cells[0];

		wall.vaultChanged(false);

		expect(wall.cells[0]).toBe(before);
	});
});

describe('what is saved', () => {
	it('carries the shape and every drawn cell, and nothing about what was playing', () => {
		const wall = new Wall();
		wall.setLayout('side_by_side_by_side');
		wall.all[0].source = 'fav:yes';
		wall.all[0].volume = 40;

		const saved = wall.preset;

		expect(saved.layout).toBe('side_by_side_by_side');
		expect(saved.cells).toHaveLength(3);
		expect(saved.cells[0]).toEqual({
			source: 'fav:yes',
			media_kind: 'video_gif',
			ordering: 'shuffle',
			end_behaviour: 'loop_all',
			timer_seconds: null,
			volume: 40,
			// A fresh cell shuffles its whole source, and `ordering` above says the same thing.
			sort: 'random',
			// The shape a cell is held to. Dynamic is the default: the cell takes the shape of
			// whatever it happens to be playing.
			aspect: 'dynamic'
		});
		expect(Object.keys(saved.cells[0])).not.toContain('playing');
	});

	it('takes a saved wall up, shape and all', () => {
		const wall = new Wall();

		wall.adopt({
			layout: 'grid',
			cells: [
				{
					source: 'in:holiday',
					media_kind: 'all',
					ordering: 'in_order',
					end_behaviour: 'loop_all',
					timer_seconds: 20,
					volume: 10,
					sort: null,
					// A shape a saved wall carries, so adopting one is checked to bring it across
					// rather than only checked to draw the grid.
					aspect: 'tall'
				},
				...Array.from({ length: 3 }, () => ({
					source: '',
					media_kind: 'video_gif' as const,
					ordering: 'shuffle' as const,
					end_behaviour: 'once' as const,
					timer_seconds: null,
					volume: 100,
					sort: null,
					aspect: 'dynamic'
				}))
			]
		});

		expect(wall.layout).toBe('grid');
		expect(wall.cells[0].source).toBe('in:holiday');
		expect(wall.cells[0].timerSeconds).toBe(20);
		expect(wall.cells[0].endBehaviour).toBe('loop_all');
		// The shape comes across too. A wall of one tall cell beside two wide ones is most of what
		// makes a wall worth saving, and it is the cell that carries it rather than the layout.
		expect(wall.cells[0].aspect).toBe('tall');
		expect(wall.cells[1].aspect).toBe('dynamic');
	});

	it('falls back to a shape it knows when a stored one means nothing here', () => {
		const wall = new Wall();

		wall.adopt({ layout: 'hexagon', cells: [] });

		expect(wall.layout).toBe('side_by_side_by_side');
	});
});

describe('building the wall', () => {
	/* The model in one line: a wall grows through the four shapes the picker offers, and a wall
	 * already at the largest of them grows its strip instead. The ladder itself is tested where it
	 * lives, in `layouts`; what is here is the part the wall owns: which cells are drawn, where a
	 * new one lands, and what happens to the settings on the cells around it. */

	it('steps up to the next shape the picker offers, and draws the new cell', () => {
		const wall = new Wall();
		expect(wall.layout, 'a wall opens on the third rung').toBe('side_by_side_by_side');

		wall.spawn();

		expect(wall.cells).toHaveLength(4);
		expect(wall.layout, 'a built wall is still one of the shapes').toBe('grid');
	});

	it('walks the whole ladder and then grows the strip', () => {
		/* The ladder end to end, because each rung is a different arithmetic: one to two and two to
		   three add a column, three to four adds a row AND moves the third cell to the row below it,
		   and four is where the wall stops being able to hold another big picture. */
		const wall = new Wall();
		wall.setLayout('single');

		wall.spawn();
		expect(wall.layout).toBe('side_by_side');
		wall.spawn();
		expect(wall.layout).toBe('side_by_side_by_side');
		wall.spawn();
		expect(wall.layout, 'the top of the ladder').toBe('grid');

		wall.spawn();
		expect(wall.inFocus, 'the wall itself takes no more').toHaveLength(4);
		expect(wall.previews, 'so the new cell is a preview').toHaveLength(1);
	});

	it('grows the same way whichever cell was pressed, because it is not told', () => {
		/* A feed does not put another one beside, above or below itself: the same wall would
		   grow into a different shape depending on which cell was pressed and which way: two
		   presses could reach an L. The rung is the answer, and `spawn` takes no cell and no
		   side: two walls grown once are the same wall. */
		const shapes = [0, 1].map(() => {
			const wall = new Wall();
			wall.spawn();
			return wall.shape;
		});

		for (const shape of shapes) expect(shape).toEqual(shapes[0]);
		expect(shapes[0].slots).toHaveLength(4);
	});

	it('will not go below one feed', () => {
		const wall = new Wall();
		wall.remove(0);
		wall.remove(0);

		expect(wall.cells, 'a wall with nothing on it is not a state anybody meant').toHaveLength(1);
		expect(wall.canShrink).toBe(false);
	});

	it('stops at the ceiling, and stops the wall itself four rungs before it', () => {
		const wall = new Wall();
		for (let n = 0; n < MOST_CELLS + 3; n += 1) wall.spawn();

		expect(wall.cells, 'nine is the wall').toHaveLength(MOST_CELLS);
		expect(wall.inFocus, 'four of which are the wall itself').toHaveLength(4);
		expect(wall.previews, 'and five are the strip').toHaveLength(5);
		expect(wall.canGrow).toBe(false);
		expect(wall.canPreview).toBe(false);
	});

	it('takes the settings of the cell that was REMOVED, not of the last one', () => {
		/*
		 * The fault this is here for. The cells are a fixed list and the shape says how many of them
		 * are drawn, so removing the FIRST of three by simply drawing one fewer would leave cell 1
		 * on screen still holding what was set on it, and cells 2 and 3 would shuffle up into
		 * places holding somebody else's sources. The wrong cell disappears, and every setting after
		 * it moves one place.
		 */
		const wall = new Wall();
		wall.setLayout('side_by_side_by_side');
		wall.all[0].source = 'first';
		wall.all[1].source = 'second';
		wall.all[2].source = 'third';

		wall.remove(0);

		expect(wall.cells.map((one) => one.source)).toEqual(['second', 'third']);
	});

	/*
	 * DUPLICATING A CELL, which is three properties and not one.
	 *
	 * The copy has to carry what the original was set to; it has to land in the place AFTER it, not
	 * at the end of the wall; and every cell past it has to keep its own settings. The middle one is
	 * what a duplicate that only grew the shape gets wrong: the next rung's new place is at the
	 * END, so the new picture would appear at the far end and everything else would shuffle along.
	 */
	it('puts the copy in the place after the original, carrying what it was set to', () => {
		const wall = new Wall();
		wall.setLayout('side_by_side_by_side');
		wall.all[0].source = 'in:holiday';
		wall.all[0].aspect = 'tall';
		wall.all[0].timerSeconds = 20;
		wall.all[1].source = 'second';
		wall.all[2].source = 'third';

		wall.duplicate(0);

		expect(wall.layout, 'and the wall is one rung larger').toBe('grid');
		expect(wall.cells).toHaveLength(4);
		expect(wall.cells.map((one) => one.source)).toEqual([
			'in:holiday',
			'in:holiday',
			'second',
			'third'
		]);
		// The shape travels with the source. A cell IS what `saved` carries, and a copy that took the
		// source and left the rest is a different cell wearing the same filter.
		expect(wall.cells[1].aspect).toBe('tall');
		expect(wall.cells[1].timerSeconds).toBe(20);
		// And the original is untouched by having been copied.
		expect(wall.cells[0].aspect).toBe('tall');
	});

	it('draws the copy in the place beside the original', () => {
		/* The settings travel, not the slot. The rung's new place is at the END of the shape, so a
		   duplicate that simply wrote the copy into the new cell would draw it wherever the last
		   cell is: the wall shuffling itself around a gesture that should only have added to it.
		   Every later cell's settings slide down by one instead. */
		const wall = new Wall();
		// Two feeds, so the copy's rung adds a column beside the original rather than a row below.
		wall.setLayout('side_by_side');
		wall.all[0].source = 'first';
		wall.all[1].source = 'second';
		const wasSecond = { ...wall.shape.slots[1] };

		wall.duplicate(0);

		const copy = wall.shape.slots[1];
		const moved = wall.shape.slots[2];
		expect(copy.col, 'the copy sits in the column opened next to the original').toBe(1);
		expect(moved.col, 'and the cell that was there keeps the place it was drawn in').toBe(
			wasSecond.col + 1
		);
		expect(wall.cells[2].source).toBe('second');
	});

	it('duplicates the only cell there is into a wall of two', () => {
		// The first rung, which is the one a person meets first: one feed, copied, is two side by
		// side with the copy in the second place.
		const wall = new Wall();
		wall.setLayout('single');
		wall.all[0].source = 'in:holiday';

		wall.duplicate(0);

		expect(wall.layout).toBe('side_by_side');
		expect(wall.cells.map((one) => one.source)).toEqual(['in:holiday', 'in:holiday']);
	});

	it('sends the copy to the strip when the wall itself is full, with what it was set to', () => {
		/* Not a refusal: a full wall answering false would have the menu turn that into a toast,
		   a press that teaches you the wall is full and does nothing else. The copy goes into the
		   strip, carrying the original's settings, which is what a duplicate means. */
		const wall = new Wall();
		wall.setLayout('grid');
		wall.all[1].source = 'in:holiday';
		wall.all[1].aspect = 'tall';
		wall.all[1].timerSeconds = 20;
		expect(wall.layout, 'the wall has to be at the top rung for this to prove anything').toBe(
			'grid'
		);

		wall.duplicate(1);

		expect(wall.inFocus, 'the wall itself is unchanged').toHaveLength(4);
		expect(wall.previews, 'and the copy is a preview').toHaveLength(1);
		expect(wall.previews[0].source).toBe('in:holiday');
		expect(wall.previews[0].aspect, 'a copy is everything the cell was set to').toBe('tall');
		expect(wall.previews[0].timerSeconds).toBe(20);
		expect(wall.cells[1].source, 'and the original is untouched').toBe('in:holiday');
	});

	/*
	 * A DUPLICATE IS A COPY OF WHAT IS ON SCREEN, which is what the word says.
	 *
	 * `adopt` throws the run away and picks something fresh out of the source, so a duplicate
	 * made through it would put a DIFFERENT file beside the cell you were watching. The promotion
	 * out of the strip draws the same distinction with `takeOver` (see 'brings up the file the
	 * preview had'); this is the same distinction on the same wall, one gesture away.
	 */
	it('opens the copy on the clip the original has up, not on a fresh one', () => {
		const wall = new Wall();
		wall.all[0].source = 'in:holiday';
		const showing = { id: 'the-one-on-screen' } as unknown as Playable;
		wall.all[0].playing = showing;
		/* On the CLASS rather than on one object: which cell becomes the copy is `duplicate`'s own
		   arithmetic (the spare at the end of the larger shape), and a test that worked it out here
		   would be a second copy of that rule, and would go on passing if the rule moved. What is
		   under test is which METHOD was reached for. */
		const taking = vi.spyOn(Cell.prototype, 'takeOver').mockResolvedValue();
		const adopting = vi.spyOn(Cell.prototype, 'adopt').mockImplementation(() => {});

		wall.duplicate(0);

		expect(taking).toHaveBeenCalledWith(expect.objectContaining({ source: 'in:holiday' }), showing);
		expect(adopting, 'adopt would have picked a different file').not.toHaveBeenCalled();
		/* Put back by hand: these two are on the CLASS, and this file has no `restoreMocks`, so
		   a `takeOver` left mocked would make every later test in the file duplicate into a cell
		   that never takes its source. */
		taking.mockRestore();
		adopting.mockRestore();
	});

	it('sends the same clip to the strip when the wall is full, rather than a fresh one', () => {
		// The other half of `duplicate`, under the same rule: at the top rung the copy goes
		// into the strip, and a strip showing a different file is the same fault in a smaller box.
		const wall = new Wall();
		wall.setLayout('grid');
		wall.all[1].source = 'in:holiday';
		const showing = { id: 'the-one-on-screen' } as unknown as Playable;
		wall.all[1].playing = showing;
		const taking = vi.spyOn(Cell.prototype, 'takeOver').mockResolvedValue();
		const adopting = vi.spyOn(Cell.prototype, 'adopt').mockImplementation(() => {});

		wall.duplicate(1);

		expect(wall.previews).toHaveLength(1);
		expect(taking).toHaveBeenCalledWith(expect.objectContaining({ source: 'in:holiday' }), showing);
		expect(adopting).not.toHaveBeenCalled();
		taking.mockRestore();
		adopting.mockRestore();
	});

	it('does nothing for a preview, which is not a place in the shape', () => {
		const wall = new Wall();
		wall.setLayout('center_stage');
		const previewAt = wall.shape.slots.length;
		expect(wall.isPreview(previewAt), 'this test is about a preview position').toBe(true);
		const before = wall.cells.length;

		wall.duplicate(previewAt);

		expect(wall.cells, 'the strip is grown by its own verbs, not by this one').toHaveLength(before);
	});

	/*
	 * THE CELLS MOVE, AND THAT IS WHAT KEEPS A COPY PLAYING.
	 *
	 * Duplicate a cell, close the one it was copied from, and the copy must not start its file
	 * again from the beginning, which is what sliding the settings one place along through
	 * `adopt` would do, since `adopt` throws the run away and finds something fresh. Identity is
	 * what these two pin, because a cell is drawn under its own key: the same cell is the same
	 * picture, with its element, its playhead and its place in the run still in it.
	 */
	it('keeps the copy itself when the cell it was copied from is closed', () => {
		const wall = new Wall();
		wall.setLayout('single');
		wall.all[0].source = 'in:holiday';

		wall.duplicate(0);
		const copy = wall.cells[1];

		wall.remove(0);

		expect(wall.cells, 'the wall healed back to one feed').toHaveLength(1);
		expect(wall.cells[0], 'the copy was thrown away and its filter moved into another cell').toBe(
			copy
		);
		expect(wall.cells[0].source).toBe('in:holiday');
	});

	it('leaves every other feed in the cell it was already drawn in', () => {
		const wall = new Wall();
		wall.setLayout('side_by_side_by_side');
		const [first, second, third] = wall.cells;

		wall.duplicate(0);

		expect(wall.cells[0], 'the original moved for a gesture that only adds').toBe(first);
		expect(wall.cells[2], 'the feed beside it was restarted to make room').toBe(second);
		expect(wall.cells[3]).toBe(third);
	});

	it('comes back DOWN the ladder when a cell is taken out, and keeps the feeds in order', () => {
		/* The fault this guards: a grid with one cell taken out healing into one wide cell under
		   two, so three portrait feeds are drawn two over one. Three is a rung, and the wall is it. */
		const wall = new Wall();
		wall.setLayout('grid');
		const [, second, third, fourth] = wall.cells;
		expect(wall.layout).toBe('grid');

		wall.remove(0);
		expect(wall.layout).toBe('side_by_side_by_side');
		expect(wall.cells[0]).toBe(second);
		expect(wall.cells[1]).toBe(third);
		expect(wall.cells[2]).toBe(fourth);
	});

	it('is in no preset once a cell is taken out of a wall past the top of the ladder', () => {
		/* Neither adding nor removing can reach a shape that is not a preset any more, so the wall
		   that has no name is a SAVED custom one of more than four: six, less one, heals. */
		const wall = new Wall();
		wall.adopt({ layout: 'custom', shape: writeShape(SIX), cells: [] });
		expect(wall.cells).toHaveLength(6);

		wall.remove(0);
		expect(wall.cells).toHaveLength(5);
		expect(wall.layout, 'a healed wall is not one of the presets').toBeNull();
	});

	it('saves a built wall under a name no preset answers to, in the shape the SERVER spells', () => {
		/* The spelling is the point of the second assertion. A browser that sent `rowSpan` where the
		   server asks for `row_span` would have every save refused with a 422, and the reader takes
		   either, so a wall saved and read back by the browser alone round-trips and hides it. */
		const wall = new Wall();
		wall.adopt({ layout: 'custom', shape: writeShape(SIX), cells: [] });
		wall.remove(0);

		expect(wall.preset.layout).toBe('custom');
		expect(wall.preset.shape).toEqual(writeShape(wall.shape));
		expect(wall.preset.shape.slots[0]).toHaveProperty('row_span');
		expect(wall.preset.shape.slots[0]).not.toHaveProperty('rowSpan');
	});

	it('opens a wall saved before shapes existed, from its preset name', () => {
		const wall = new Wall();
		wall.adopt({ layout: 'grid', cells: [] });

		expect(wall.cells, 'an older saved wall no longer opens').toHaveLength(4);
	});

	it('falls back to the preset when a stored shape cannot be drawn', () => {
		const wall = new Wall();
		wall.adopt({ layout: 'grid', shape: { rows: 1, cols: 1, slots: 'nonsense' }, cells: [] });

		expect(wall.cells).toHaveLength(4);
		expect(wall.layout).toBe('grid');
	});

	it('opens a wall saved stacked as two cells, one over the other', () => {
		const wall = new Wall();
		wall.adopt({ layout: 'stacked', cells: [] });

		expect(wall.cells).toHaveLength(2);
		expect([wall.shape.rows, wall.shape.cols]).toEqual([2, 1]);
		expect(wall.layout).toBe('stacked');
	});
});

describe('stopping and starting everything', () => {
	it('starts a cell that had been stopped on its own', () => {
		/* The fault this guards: a cell stopped by pressing its picture keeps its own hold, and a
		   play control that only touched the wall's would leave that one cell frozen under "Play
		   everything" while the control read "Pause everything", with nothing saying why. */
		const wall = new Wall();
		wall.all[1].paused = true;

		wall.togglePause();

		expect(wall.paused, 'the wall did not start').toBe(false);
		expect(
			wall.all[1].paused,
			'a cell stopped by hand stayed stopped through "Play everything"'
		).toBe(false);
	});

	it('leaves the hold a cell set for itself alone when everything is stopped', () => {
		/* Stopping must NOT write the wall's hold into every cell. Which cell somebody deliberately
		   stopped is the one thing a cell's hold carries, and it is wanted again the moment the wall
		   starts: overwriting it would make a pause-then-play quietly restart it. */
		const wall = new Wall();
		wall.togglePause();
		wall.all[1].paused = true;

		wall.togglePause();

		expect(wall.paused).toBe(true);
		expect(wall.all[1].paused, 'stopping the wall rewrote what a cell had set itself').toBe(true);
		expect(wall.all[0].paused, 'stopping the wall stopped a cell that was not stopped').toBe(false);
	});
});

describe('coming back to a wall that is already running', () => {
	/* The wall outlives the screen (that is what lets the corner panel keep it playing while
	   somebody is on another page), so returning to Theater opens the SAME wall a second time. */

	it('does not stop a wall that was already playing', async () => {
		const wall = new Wall();
		await wall.open();
		wall.togglePause();
		expect(wall.paused, 'the wall was not running to begin with').toBe(false);

		await wall.open();

		expect(wall.paused, 'coming back out of the corner panel paused everything').toBe(false);
	});

	it('still decides the first time, from the account preference', async () => {
		const wall = new Wall();
		await wall.open();

		// Autoplay is off unless the account says otherwise, so a wall arriving is held.
		expect(wall.paused).toBe(true);
	});
});

describe('leaving Theater and coming back', () => {
	const FILE = { id: 'clip-1', media_type: 'video' } as unknown as Playable;

	afterEach(() => {
		vi.restoreAllMocks();
		vault.unlocked = false;
	});

	/* A wall set up and left: two across, the first cell on a clip forty seconds in. */
	function leave(resumes: boolean) {
		const wall = showing.ensure();
		wall.setLayout('side_by_side');
		wall.cells[0].playing = FILE;
		wall.cells[0].position = 40;
		wall.focused = 1;
		wall.resumes = resumes;
		showing.drop(false);
		return wall;
	}

	it('comes back to the same layout, each cell on what it had up, from where it had reached', () => {
		const takeOver = vi.spyOn(Cell.prototype, 'takeOver').mockResolvedValue();
		const left = leave(true);

		const back = showing.ensure();

		expect(back).not.toBe(left);
		expect(back.cells).toHaveLength(2);
		expect(back.focused).toBe(1);
		expect(takeOver.mock.calls[0][1]).toEqual(FILE);
		expect(takeOver.mock.calls[0][2]).toBe(40);
		expect(takeOver.mock.calls[1][1]).toBeNull();
		showing.drop(false);
	});

	it('starts afresh when the account does not keep it', () => {
		const takeOver = vi.spyOn(Cell.prototype, 'takeOver').mockResolvedValue();
		leave(false);

		showing.ensure();

		expect(takeOver).not.toHaveBeenCalled();
		showing.drop(false);
	});

	it('lets the kept wall go when the setting is turned off while away', () => {
		const takeOver = vi.spyOn(Cell.prototype, 'takeOver').mockResolvedValue();
		leave(true);

		showing.resumeChanged(false);
		showing.ensure();

		expect(takeOver).not.toHaveBeenCalled();
		showing.drop(false);
	});

	describe('across a reload', () => {
		const STORED = 'sift.theater.kept.account-a';

		beforeEach(() => {
			session.viewer = { id: 'account-a' } as Viewer;
			sessionStorage.clear();
		});

		afterEach(() => {
			session.viewer = undefined;
			sessionStorage.clear();
		});

		it('writes the wall to the tab by file id, never by name', () => {
			const named = { ...FILE, original_filename: 'holiday.mp4' } as Playable;
			const wall = showing.ensure();
			wall.setLayout('side_by_side');
			wall.cells[0].playing = named;
			wall.cells[0].position = 40;
			wall.resumes = true;

			window.dispatchEvent(new Event('pagehide'));

			const raw = sessionStorage.getItem(STORED) ?? '';
			expect(JSON.parse(raw).playing[0]).toEqual({
				id: 'clip-1',
				at: 40,
				seed: wall.cells[0].seed
			});
			expect(raw, 'a file name was written into the browser').not.toContain('holiday');
			showing.drop(false);
		});

		it('takes the stored wall up once, each cell asking for its file by id', () => {
			const resumeOn = vi.spyOn(Cell.prototype, 'resumeOn').mockResolvedValue();
			leave(true);
			const stored = sessionStorage.getItem(STORED);
			expect(stored, 'leaving Theater wrote nothing to the tab').not.toBeNull();
			// A reload: the page's memory is gone and only the tab's storage is left.
			showing.resumeChanged(false);
			sessionStorage.setItem(STORED, stored as string);

			const back = showing.ensure();

			expect(back.cells).toHaveLength(2);
			expect(back.focused).toBe(1);
			expect(resumeOn.mock.calls[0].slice(1)).toEqual(['clip-1', 40]);
			expect(sessionStorage.getItem(STORED), 'the stored wall was taken up twice').toBeNull();
			showing.drop(false);
		});

		it("never gives one account another's wall", () => {
			const resumeOn = vi.spyOn(Cell.prototype, 'resumeOn').mockResolvedValue();
			leave(true);
			const stored = sessionStorage.getItem(STORED) as string;
			showing.resumeChanged(false);
			sessionStorage.setItem(STORED, stored);
			session.viewer = { id: 'account-b' } as Viewer;

			showing.ensure();

			expect(resumeOn).not.toHaveBeenCalled();
			showing.drop(false);
		});

		it('forgets the stored wall when the setting is turned off', () => {
			leave(true);
			expect(sessionStorage.getItem(STORED)).not.toBeNull();

			showing.resumeChanged(false);

			expect(sessionStorage.getItem(STORED)).toBeNull();
		});
	});

	it('does not bring back a wall kept over an open vault once the vault has shut', () => {
		const takeOver = vi.spyOn(Cell.prototype, 'takeOver').mockResolvedValue();
		vault.unlocked = true;
		leave(true);
		vault.unlocked = false;

		showing.ensure();

		expect(takeOver).not.toHaveBeenCalled();
		showing.drop(false);
	});
});

describe('silencing everything, and letting it back', () => {
	/*
	 * THE MASTER MUTE IS THE SECOND OF TWO, AND LIFTING IT HAS TO BE HEARD.
	 *
	 * Every cell starts muted on its own, deliberately: no browser begins several videos with
	 * sound unasked. So a wall at rest is silent twice over, and a `toggleMaster` that touched only
	 * one of them would flip a flag with nothing coming out of the speakers: a control that reads
	 * as broken.
	 */
	it('lets the sound back on every cell, not just on the wall', () => {
		const wall = new Wall();
		expect(wall.masterMuted, 'a wall does not start silenced').toBe(true);
		expect(wall.all[0].muted, 'a cell does not start muted').toBe(true);

		wall.toggleMaster();

		expect(wall.masterMuted).toBe(false);
		expect(wall.isAudible(0), 'letting the sound back left the cell muted on its own').toBe(true);
		expect(wall.isAudible(1)).toBe(true);
	});

	/*
	 * AND IT UNMUTES EVERY CELL, not only the ones that were already unmuted.
	 *
	 * Clearing a cell's own mute only when every cell is muted (so a mute set on one cell an hour
	 * ago survives a silence-and-back) makes one control do two different things depending on
	 * state nobody can see. "Unmute all" means all.
	 */
	it('unmutes every cell, including one muted by hand', () => {
		const wall = new Wall();
		wall.toggleMaster();
		wall.toggleMute(1);
		expect(wall.all[1].muted).toBe(true);

		wall.toggleMaster();
		wall.toggleMaster();

		expect(wall.all[1].muted, 'unmute all left a cell muted').toBe(false);
		expect(wall.all[0].muted).toBe(false);
	});

	/* Silencing still writes nothing into a cell: a wall that is silent is silent either way, and
	   the master alone is enough to make it so. */
	it('silences without writing into each cell', () => {
		const wall = new Wall();
		wall.toggleMaster();

		wall.toggleMaster();

		expect(wall.masterMuted).toBe(true);
		expect(wall.all[0].muted, 'silencing everything wrote the mute into the cell as well').toBe(
			false
		);
	});

	/* Somebody has to be the one the operating system is told about, or the lock screen names
	   nothing while four cells are playing out loud. */
	it('points the sound at the cell the keyboard is on when it comes back', () => {
		const wall = new Wall();
		wall.focus(1);

		wall.toggleMaster();

		expect(wall.heard).toBe(1);
	});

	/*
	 * ONE ANSWER FOR ALL OF THEM, which is what makes a press that addresses the whole wall mean
	 * anything. Flipping each cell to the opposite of itself leaves a wall exactly as it was with
	 * the two halves swapped, which is the one outcome nobody presses a mute for.
	 */
	it('gives every named cell the same answer rather than flipping each', () => {
		const wall = new Wall();
		// Three PLACES, because three are named below. Naming a cell the wall is not drawing would
		// mute something nobody can hear. Positions are what a press addresses; see `Wall.at`.
		wall.setLayout('side_by_side_by_side');
		wall.toggleMaster();
		wall.toggleMute(1);

		wall.setMuted([0, 1, 2], true);

		expect(wall.cells.map((cell) => cell.muted)).toEqual([true, true, true]);

		wall.setMuted([0, 1, 2], false);

		expect(wall.cells.map((cell) => cell.muted)).toEqual([false, false, false]);
	});
});

describe('talking to every cell in one go', () => {
	/* The backtick beside the numbers. A SELECTION rather than a second set of shortcuts, so every
	   verb the bar and the keyboard already have reaches the whole wall without learning a key. */

	it('addresses one cell until it is told otherwise', () => {
		const wall = new Wall();
		wall.focus(1);

		expect(wall.addressedAt).toEqual([1]);
		expect(wall.addressed).toEqual([wall.all[1]]);
	});

	it('addresses every drawn cell, and only the drawn ones', () => {
		const wall = new Wall();
		wall.focusEvery();

		expect(wall.everyCell).toBe(true);
		expect(wall.addressedAt).toEqual(wall.cells.map((_, at) => at));
		expect(
			wall.addressedAt.length,
			'a wall addressed every cell it HAS rather than every cell it draws'
		).toBeLessThan(MOST_CELLS);
	});

	it('comes off every cell the moment one is named', () => {
		const wall = new Wall();
		wall.focusEvery();

		wall.focus(1);

		expect(wall.everyCell, 'naming one cell left the wall addressing all of them').toBe(false);
		expect(wall.addressedAt).toEqual([1]);
	});

	/*
	 * THE FLASH IS FOR THE NUMBER, NOT FOR THE PRESS.
	 *
	 * Pressing a picture says which cell you meant by being the thing under the pointer; pressing
	 * `3` says it with a digit, and on a wall of nine there is nothing joining the two until the
	 * wall answers. So the counter the wash replays on is bumped by one of the two and not the
	 * other: if `focus` bumped it, every click on a cell would flash.
	 */
	it('counts a choice made by number, and not a press on the picture', () => {
		const wall = new Wall();
		const before = wall.chosenTimes;

		wall.focus(1);
		expect(wall.chosenTimes, 'pressing a cell flashed it').toBe(before);

		wall.chooseByNumber(1);
		expect(wall.chosenTimes).toBe(before + 1);

		// Again, on the cell that is already chosen: it has to count, or a second press reads as
		// having been ignored.
		wall.chooseByNumber(1);
		expect(wall.chosenTimes).toBe(before + 2);

		wall.focusEvery();
		expect(wall.chosenTimes).toBe(before + 3);
	});

	it('marks every cell while the whole wall is being aimed at', () => {
		const wall = new Wall();
		wall.aiming = 1;

		expect(wall.aimedAt(1)).toBe(true);
		expect(wall.aimedAt(0)).toBe(false);

		wall.aiming = 'every';

		expect(wall.aimedAt(0)).toBe(true);
		expect(wall.aimedAt(1)).toBe(true);

		wall.aiming = null;

		expect(wall.aimedAt(0)).toBe(false);
	});
});
