/* WHAT THE BAR IS ABOUT TO ACT ON, SAID ON THE WALL.
 *
 * One bar drives whichever cell is chosen, out of up to nine on screen, and nothing on the bar
 * itself can say which one: the answer is a rectangle somewhere else entirely. The filter button
 * washes its target; everything else here that acts on a cell must too, or the same press could
 * land on a different picture from the one somebody had in mind.
 *
 * These are about the REPORT rather than about the wash: `Wall.aiming` is what the cells read, and
 * `CellView` draws it. What is pinned here is that pointing at a verb writes it, that leaving
 * clears it, and that the All chip makes it every cell rather than one.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(async () => ({ items: [], total: 0 })), post: vi.fn(async () => ({})) }
}));

import { api } from '$lib/api/client';
import CellControls from './CellControls.svelte';
import cellControlsSource from './CellControls.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import { Wall } from '$lib/theater/wall.svelte';

let host: HTMLElement;
let running: Record<string, unknown>;
/* Whether every cell has a file before the one it shows, so Previous has somewhere to go. */
let behind = true;

/*
 * The bar as `StallBar` draws it: `index` is the CHOSEN cell, which is what that caller always
 * hands in. Passing one number and choosing another would be testing an arrangement the app never
 * makes.
 */
function draw(at = 0, everyCell = false, real = false) {
	const wall = new Wall();
	if (!real)
		for (const one of wall.all) Object.defineProperty(one, 'hasBack', { get: () => behind });
	wall.focused = at;
	if (everyCell) wall.focusEvery();
	const cell = wall.all[at];
	host = document.createElement('div');
	document.body.append(host);
	running = mount(CellControls, {
		target: host,
		props: {
			wall,
			cell,
			index: at,
			position: 0,
			duration: 10,
			onseek: () => {},
			filled: true
		}
	});
	flushSync();
	return wall;
}

/** A control on the bar, found by the name a person hears rather than by where it sits. */
function control(name: string): HTMLElement {
	const found = host.querySelector(`button[aria-label="${name}"]`);
	if (!found) throw new Error(`no control called ${name}`);
	return found as HTMLElement;
}

function point(at: HTMLElement) {
	at.dispatchEvent(new MouseEvent('mouseenter', { bubbles: false }));
	flushSync();
}

function away(from: HTMLElement) {
	from.dispatchEvent(new MouseEvent('mouseleave', { bubbles: false }));
	flushSync();
}

beforeEach(() => {
	behind = true;
	vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
});

afterEach(() => {
	if (running) void unmount(running);
	host?.remove();
	vi.restoreAllMocks();
});

describe('pointing at a verb on the wall bar', () => {
	// The outer pair names the cell it lands on, so the label follows the cell being drawn.
	it.each(['Play', 'Previous in cell 3', 'Next in cell 3'])(
		'%s marks the cell it would act on, and clears it on the way out',
		(name) => {
			const wall = draw(2);

			const button = control(name);
			point(button);
			expect(wall.aiming, `pointing at ${name} marked nothing`).toBe(2);

			away(button);
			expect(wall.aiming, `leaving ${name} left the mark lit`).toBeNull();
		}
	);

	it('marks the cell from the drawer too, not only from the row', () => {
		const wall = draw(1);

		// The drawer is a grid that opens under the pointer; nothing in it exists until it does.
		control('More controls').click();
		flushSync();

		const button = control('Stats for nerds');
		point(button);
		expect(wall.aiming).toBe(1);

		away(button);
		expect(wall.aiming).toBeNull();
	});

	it('marks EVERY cell while the All chip is on', () => {
		// A verb that lands on all nine must not wash one of them: a mark that is wrong about what is
		// about to happen is worse than no mark, because it is read.
		const wall = draw();
		wall.focusEvery();

		point(control('Play'));
		expect(wall.aiming).toBe('every');
	});

	it('reads the selection when the pointer ARRIVES rather than when the bar was drawn', () => {
		// The two are the same until somebody presses All with the pointer already on the bar, which
		// is the case a value captured at draw time gets wrong.
		const wall = draw(1);
		const button = control('Play');

		point(button);
		expect(wall.aiming).toBe(1);

		away(button);
		wall.focusEvery();
		point(button);
		expect(wall.aiming).toBe('every');
	});

	it('says nothing for a control that is about the WALL rather than about a cell', () => {
		// Center stage's mode is a property of the wall: no cell changes, so no cell is washed. A
		// mark that appears for everything means nothing.
		const wall = draw();
		wall.addPreview();
		flushSync();
		control('More controls').click();
		flushSync();
		const mode = host.querySelector(
			'button[aria-label="A preview comes up when you double-click it"]'
		) as HTMLElement | null;
		if (!mode) throw new Error('the mode control was not drawn');

		point(mode);
		expect(wall.aiming, 'a control that changes no cell washed one').toBeNull();
	});
});

/*
 * WHAT "EVERY CELL" ACTUALLY REACHES, which is a judgement, pinned here.
 *
 * The backtick and the All chip put every verb on the whole wall: the transport, the sound, the
 * volume, the two steps and five seconds either way. Deliberately NOT reached: the scrubber, the
 * A-B marks, Save as Loop, Quality and Hear only this.
 *
 * The line between them is the difference between a RELATIVE move and an ABSOLUTE position. "Five
 * seconds back" is true of nine clips at once; "four minutes and eleven seconds in" is true of
 * exactly the one whose timeline is drawn. A selection that reached the wrong half of the bar is
 * worse than no selection at all, so which half is which is checked here.
 */
describe('what a verb lands on while every cell is selected', () => {
	/* Every CELL stopped over a wall that is not.
	 *
	 * The wall's own hold wins over a cell's: pressing play while everything is stopped means
	 * start, and starting one cell out of a stopped wall is not a thing to guess at. And a wall
	 * opens held. So a fixture that only pauses the cells is testing the wall's hold instead, and
	 * every cell comes back however the bar is aimed. */
	function stopEveryCell(wall: Wall) {
		wall.paused = false;
		wall.cells.forEach((one) => (one.paused = true));
		flushSync();
	}

	it('the transport reaches every cell, not the one the bar is drawing', () => {
		const wall = draw(0, true);
		stopEveryCell(wall);

		control('Play').click();
		flushSync();

		expect(
			wall.cells.map((one) => one.paused),
			'a relative verb is true of the whole wall at once'
		).toEqual(wall.cells.map(() => false));
	});

	it('and only the chosen cell while one cell is chosen', () => {
		// The positive control. Without it, a transport that always reached everything would pass.
		const wall = draw(0);
		stopEveryCell(wall);

		control('Play').click();
		flushSync();

		expect(wall.cells[0].paused, 'the cell on the bar did not start').toBe(false);
		expect(
			wall.cells.slice(1).every((one) => one.paused),
			'a verb aimed at one cell moved the rest of the wall'
		).toBe(true);
	});

	/* The names, which are the whole of what a screen reader and a speech command have. An icon-only
	   button saying "cell 2" while it is about to act on nine is the one kind of wrong label that
	   cannot be recovered from by looking at the screen. */
	it('every verb that reaches the wall says so in its name', () => {
		draw(0, true);

		const named = [...host.querySelectorAll('button[aria-label]')].map((one) =>
			one.getAttribute('aria-label')
		);
		for (const said of ['Previous in every cell', 'Next in every cell']) {
			expect(named, `${said} was not on the bar`).toContain(said);
		}
	});

	it('and names the one cell it is on when that is what it is', () => {
		draw(0);

		const named = [...host.querySelectorAll('button[aria-label]')].map((one) =>
			one.getAttribute('aria-label')
		);
		expect(named).toContain('Previous in cell 1');
		expect(named, 'a verb claimed the whole wall over one chosen cell').not.toContain(
			'Previous in every cell'
		);
	});

	/* THE OTHER HALF, and the one that makes the rest worth having. The scrubber is an absolute
	   position on ONE clip's timeline, so it stays with the cell the bar draws however the wall is
	   selected: there is no honest way to apply "four minutes in" to nine different clips. */
	it('the scrubber stays with the cell the bar is drawing, even then', () => {
		draw(0, true);

		const timeline = host.querySelector('.timeline input[type="range"]');
		expect(timeline, 'the bar drew no scrubber to check').not.toBeNull();
		expect(
			timeline?.getAttribute('aria-label'),
			'the scrubber offered a position on a wall, which is not a thing that has one'
		).toBe('Position in cell 1');
	});
});

describe('the control that plays something else', () => {
	/*
	 * Its verb is "else", and the bar has to ask for that one. `restart` means "start again from
	 * the top of what this cell draws from", which from the top of a shuffle already in hand is the
	 * file on screen. `Cell.somethingElse` is the verb meaning not this one; what the press is
	 * wired to is the whole difference, so it is pinned here rather than left to the cell's own
	 * tests.
	 */
	it('asks the addressed cell for something else, not for the top of its own run', () => {
		const wall = draw(1);
		const cell = wall.all[1];
		const elsewhere = vi.spyOn(cell, 'somethingElse').mockResolvedValue(undefined);
		const again = vi.spyOn(cell, 'restart').mockResolvedValue(undefined);

		control('More controls').click();
		flushSync();
		control('Randomize in cell 2').click();

		expect(elsewhere).toHaveBeenCalledTimes(1);
		expect(again, 'the press started the same run over instead').not.toHaveBeenCalled();
	});
});

/*
 * A HELD WALL THAT STILL HAS SOMETHING MOVING IN IT SAYS SO.
 *
 * Where the browser will not decode a GIF frame by frame (which is any page not on https
 * and not on the machine Sift runs on), a cell draws the ordinary `<img>`, and that animates
 * whatever the wall has been told. The hold is right for every clip beside it, so the control
 * stays and the bar says what it could not reach. jsdom has no `ImageDecoder`, which is exactly
 * the browser this is about; the opposite case is made by putting one there.
 */

/*
 * THE DRAWER IS ONE SHAPE.
 *
 * Save as Loop, Quality and Center stage's mode each apply only some of the time. Drawn only then,
 * the drawer would grow and shrink between a video and a GIF and every icon after them would move. They are
 * always drawn and dimmed with the reason as their label instead.
 */
describe('the clock on a cell', () => {
	/** The scrub line's two times, start and end, where they are drawn. */
	const clock = () =>
		[...host.querySelectorAll('.scrub-line .time .reading')].map((one) => one.textContent);

	/* A photograph has no playhead and no length, so a clock under one reads 0:00 / 0:00. */
	it('is not drawn under a picture, and is under a clip, at the scrub line-s two ends', () => {
		const wall = draw();
		wall.all[0].playing = { id: 'v1', media_type: 'video' } as unknown as NonNullable<
			(typeof wall.all)[number]['playing']
		>;
		flushSync();
		expect(clock()).toEqual(['0:00', '0:10']);

		wall.all[0].playing = { id: 'p1', media_type: 'image' } as unknown as NonNullable<
			(typeof wall.all)[number]['playing']
		>;
		flushSync();
		expect(clock()).toEqual([]);
	});

	/* A cell with nothing in it has nothing to time, and 0:00 / 0:00 there reads as a stuck clip. */
	it('is not drawn under a cell with no file', () => {
		draw();
		expect(clock()).toEqual([]);
	});

	/* One bar drives every cell, so the clock's room stays when the chosen cell has no clock. */
	it('keeps the room of the clock when there is none to show', () => {
		const wall = draw();
		const room = host.querySelector('.time');
		expect(room?.classList.contains('held-room')).toBe(true);

		wall.all[0].playing = { id: 'p1', media_type: 'image' } as unknown as NonNullable<
			(typeof wall.all)[number]['playing']
		>;
		flushSync();
		const kept = host.querySelector('.time');
		expect(kept, 'the clock took its room with it').not.toBeNull();
		expect(kept?.classList.contains('held-room')).toBe(true);
		expect(kept?.getAttribute('aria-hidden')).toBe('true');
	});
});

describe('the timer row', () => {
	/* The field says a few seconds; stretched across the bar it would be a long empty box with its
	   words at the far end. */
	it('is as wide as its glyph and its field, at the start of the bar', () => {
		const wall = draw();
		wall.all[0].timing = true;
		flushSync();
		const row = host.querySelector('.cell-settings') as HTMLElement;
		expect(row, 'the timer row did not open').not.toBeNull();
		applyStyles(cellControlsSource);
		expect(getComputedStyle(row).gridTemplateColumns).toBe('auto auto');
		expect(getComputedStyle(row).justifySelf).toBe('start');
		removeStyles();
	});
});

describe('the drawer keeps one shape', () => {
	function drawer(): HTMLButtonElement[] {
		control('More controls').click();
		flushSync();
		const tray = host.querySelector('.tray [role="group"], .tray .panel') ?? host;
		return [...tray.querySelectorAll('button')].filter(
			(one) => one.getAttribute('aria-label') !== 'More controls'
		) as HTMLButtonElement[];
	}

	it('draws all twelve, Clip then Screenshot first, the four that do not apply dimmed with the reason', () => {
		draw();
		const all = drawer();
		expect(all).toHaveLength(12);
		// Where the player's drawer keeps them, and through the same two controls. Nothing is
		// playing in this cell, so Clip says so rather than going.
		expect(all[0].getAttribute('aria-label')).toBe('Nothing is playing to clip');
		expect(all[1].getAttribute('aria-label')).toBe('Screenshot');

		const off = all.filter((one) => one.disabled).map((one) => one.getAttribute('aria-label'));
		expect(off).toEqual([
			'Nothing is playing to clip',
			'Mark both ends of a loop to save it',
			'This file has one size',
			'Previews come with a Center stage layout'
		]);
	});

	it('is the same twelve once a clip has sizes, and once the wall has a strip', () => {
		const wall = draw();
		const cell = wall.all[0];
		cell.plan = {
			url: '/a',
			qualities: [
				{ url: '/a', label: 'Original' },
				{ url: '/b', label: '720p' }
			]
		} as unknown as NonNullable<typeof cell.plan>;
		flushSync();
		expect(drawer()).toHaveLength(12);
		expect(control('Quality').hasAttribute('disabled')).toBe(false);

		// A strip starts its previews, which moves every cell's plan on; the mode is what is asked.
		wall.addPreview();
		flushSync();
		expect(host.querySelectorAll('.tray .panel button')).toHaveLength(12);
		expect(control('A preview comes up when you double-click it').hasAttribute('disabled')).toBe(
			false
		);
	});
});

describe("the drawer's Clip", () => {
	/* The player's Clip on a cell: the same menu of lengths, cutting this cell's file at this
	   cell's moment, through the same door (`keepTheLast`). */
	it('cuts the last seconds of the cell file that end where the cell is, and is dimmed on a picture', async () => {
		const { api } = await import('$lib/api/client');
		const post = vi.mocked(api.post);
		post.mockClear();
		const wall = new Wall();
		wall.focused = 0;
		const cell = wall.all[0];
		cell.playing = { id: 'v1', media_type: 'video' } as unknown as NonNullable<typeof cell.playing>;
		host = document.createElement('div');
		document.body.append(host);
		running = mount(CellControls, {
			target: host,
			props: {
				wall,
				cell,
				index: 0,
				position: 12,
				duration: 30,
				onseek: () => {},
				filled: true
			}
		});
		flushSync();
		control('More controls').click();
		flushSync();

		const clip = control('Clip');
		expect((clip as HTMLButtonElement).disabled).toBe(false);
		clip.focus();
		clip.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		flushSync();
		await Promise.resolve();
		flushSync();
		const lengths = [...document.querySelectorAll('[role="menuitem"]')].map((one) =>
			one.textContent?.trim()
		);
		expect(lengths).toEqual([
			'Last 5 seconds',
			'Last 10 seconds',
			'Last 30 seconds',
			'Last 60 seconds'
		]);
		(document.querySelectorAll('[role="menuitem"]')[1] as HTMLElement).click();
		await vi.waitFor(() => expect(post).toHaveBeenCalled());
		const [path, options] = post.mock.calls[0] as [string, { body: { steps: unknown[] } }];
		expect(path).toBe('/assets/v1/edit/preflight');
		expect(options.body.steps).toEqual([{ operation: 'clip', start_ms: 2000, duration_ms: 10000 }]);

		cell.playing = { id: 'p1', media_type: 'image' } as unknown as NonNullable<typeof cell.playing>;
		flushSync();
		expect((control('Only a video can be clipped') as HTMLButtonElement).disabled).toBe(true);
	});
});

describe("the cell's timer", () => {
	/* Zero is a word, as the same setting reads under Playback, and needs no line explaining it. */
	it('calls zero No timer, with no hint under the box', async () => {
		const source = (await import('./CellControls.svelte?raw')).default;
		expect(source).toMatch(/unit="sec"\s+automatic="No timer"/);
		expect(source).not.toMatch(/zero waits for the file to end/);
	});
});

describe('Previous on the wall bar', () => {
	function tile(id: string, media_type = 'video') {
		return { id, media_type, duration_ms: 1000, thumb: false, art: null, width: 9, height: 16 };
	}

	beforeEach(() => {
		vi.mocked(api.get).mockImplementation((async (path: string) =>
			path === '/assets' ? { items: [tile('clip')], total: 1 } : { sprite: null }) as never);
		vi.mocked(api.post).mockResolvedValue({ route: 'direct', streamable: true, url: '/s' });
	});

	it('is dimmed until a cell that opened on a picture steps on, then lit', async () => {
		const wall = draw(0, false, true);
		await wall.all[0].startOn(tile('picture', 'image') as never);
		flushSync();
		expect(control('Nothing before this')).toHaveProperty('disabled', true);

		await wall.all[0].advance();
		flushSync();
		expect(wall.all[0].playing?.id).toBe('clip');
		expect(control('Previous in cell 1')).toHaveProperty('disabled', false);
	});

	it("follows the cell's own history, not the file it shows", async () => {
		const cell = new Wall().all[0];
		await cell.startOn(tile('picture', 'image') as never);
		const seen: boolean[] = [];
		const stop = $effect.root(() => {
			$effect(() => {
				seen.push(cell.hasBack);
			});
		});
		flushSync();
		await cell.advance();
		flushSync();
		stop();
		expect(seen, 'a step went unseen by what asks for Previous').toEqual([false, true]);
	});
});
