/* THE WALL'S CELLS MOVE FROM THE BOX THEY STOOD IN TO THE ONE THE PAGE GIVES THEM, ON EACH FRAME.
 *
 * jsdom lays nothing out, so the cells' boxes are said here and the frames are stepped by hand;
 * `e2e` measures the pixels.
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(async () => ({ items: [], total: 0 })), post: vi.fn(async () => ({})) },
	isMissing: (error: unknown) => (error as { status?: number } | null)?.status === 404
}));

import TheaterWall, { between } from './TheaterWall.svelte';
import { bezier } from '$lib/shell/motion.svelte';
import { Wall } from '$lib/theater/wall.svelte';
import type { PlaybackPlan } from '$lib/player/playback';
import { stage } from '$lib/components/shell/stage.svelte';
import { wallChrome } from '$lib/theater/chrome.svelte';
import { api } from '$lib/api/client';
import { arrivals, libraryChanges } from '$lib/library/changes.svelte';
import type { Playable } from '$lib/theater/cell.svelte';

let host: HTMLElement;
let running: Record<string, unknown>;

function draw() {
	const wall = new Wall();
	host = document.createElement('div');
	document.body.append(host);
	running = mount(TheaterWall, { target: host, props: { wall, onfullscreen: () => {} } });
	flushSync();
	return wall;
}

const PAGE = new DOMRect(233, 149, 1334, 727);
const SCREEN = new DOMRect(0, 70, 1600, 830);
/** Where the cells stand: in the page, or filling the screen. */
let standing = PAGE;
let filled = false;
let now = 0;
let frames: FrameRequestCallback[] = [];

/** How the screen says it has been filled, or let go: the style first, then the browser's event. */
function fills(yes: boolean) {
	standing = yes ? SCREEN : PAGE;
	filled = yes;
	stage.filling = yes;
	document.dispatchEvent(new Event('fullscreenchange', { bubbles: true }));
	flushSync();
}

/** The next frame, `ms` later. */
function frameAfter(ms: number) {
	now += ms;
	const due = frames;
	frames = [];
	for (const run of due) run(now);
	flushSync();
}

function wallOf() {
	return host.querySelector('.wall') as HTMLElement;
}

beforeEach(() => {
	stage.filling = false;
	vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
});

afterEach(() => {
	if (running) void unmount(running);
	host?.remove();
	stage.filling = false;
	vi.restoreAllMocks();
});

/** The cells' box said, the screen's state said, and the frames held: for the screen-change tests. */
function watchTheWall() {
	standing = PAGE;
	filled = false;
	now = 0;
	frames = [];
	vi.spyOn(performance, 'now').mockImplementation(() => now);
	vi.stubGlobal('requestAnimationFrame', (run: FrameRequestCallback) => frames.push(run));
	vi.stubGlobal('cancelAnimationFrame', () => (frames = []));
	vi.stubGlobal(
		'ResizeObserver',
		class {
			observe() {}
			unobserve() {}
			disconnect() {}
		}
	);
	vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
		this: HTMLElement
	) {
		if (this.classList.contains('cell')) return standing;
		return this.classList.contains('wall') ? new DOMRect(0, 0, 1920, 1080) : new DOMRect();
	});
	const closest = Element.prototype.closest;
	vi.spyOn(Element.prototype, 'closest').mockImplementation(function (
		this: Element,
		selector: string
	) {
		if (selector === ':fullscreen') return filled ? document.body : null;
		return closest.call(this, selector);
	});
}

/** Where the cells are drawn, read back off the wall's own translate and scales. */
function drawnAt(to: DOMRect) {
	const frame = wallOf();
	if (frame.style.scale === '') return to;
	const [x, y] = frame.style.translate.split(' ').map(parseFloat);
	const [sx, sy] = frame.style.scale.split(' ').map(Number);
	return new DOMRect(to.left + x, to.top + y, to.width * sx, to.height * sy);
}

describe('a file deleted elsewhere', () => {
	afterEach(() => unmount(running));

	it('leaves the cell showing it at once, and a cell whose file is there stays', async () => {
		const wall = draw();
		const [gone, kept] = wall.cells;
		gone.playing = { id: 'file-gone' } as Playable;
		kept.playing = { id: 'file-kept' } as Playable;
		const stepped = vi.spyOn(gone, 'advance').mockResolvedValue();
		const stayed = vi.spyOn(kept, 'advance').mockResolvedValue();
		vi.mocked(api.get).mockImplementation(async (path: string) => {
			if (path === '/assets/file-gone') throw { status: 404 };
			return {};
		});
		libraryChanges.changed();
		flushSync();
		await vi.waitFor(() => expect(stepped).toHaveBeenCalledOnce());
		expect(stayed).not.toHaveBeenCalled();
	});

	it('keeps the grid unseen while the opening draw has cells with no file yet', () => {
		const wall = draw();
		wall.opening = true;
		flushSync();
		const grid = host.querySelector('.grid') as HTMLElement;
		expect(grid.classList.contains('settling')).toBe(true);
		for (const cell of wall.inFocus)
			cell.coming = { id: `file-${cell.key}`, width: 9, height: 16 } as Playable;
		flushSync();
		expect(grid.classList.contains('settling')).toBe(false);
	});

	it('has a cell that found nothing look again when files arrive', () => {
		const wall = draw();
		const [empty, playing] = wall.cells;
		empty.state = 'nothing_here';
		playing.state = 'ready';
		const looked = vi.spyOn(empty, 'restart').mockResolvedValue();
		const left = vi.spyOn(playing, 'restart').mockResolvedValue();
		arrivals.changed();
		flushSync();
		expect(looked).toHaveBeenCalledOnce();
		expect(left).not.toHaveBeenCalled();
	});
});

describe('the wall around a change of screen', () => {
	beforeEach(watchTheWall);
	afterEach(() => vi.unstubAllGlobals());

	it('does not move on the first draw', () => {
		draw();
		expect(wallOf().style.translate, 'the wall moved itself into existence').toBe('');
	});

	it('draws its cells where they stood on the frame the screen fills, then eases them in', () => {
		draw();
		fills(true);
		expect(drawnAt(SCREEN), 'the cells jumped to the screen before moving').toEqual(PAGE);
		frameAfter(160);
		const halfway = bezier([0.2, 0, 0, 1])(0.5);
		expect(drawnAt(SCREEN).top).toBeCloseTo(PAGE.top + (SCREEN.top - PAGE.top) * halfway);
		frameAfter(160);
		expect(wallOf().style.translate, 'the movement outlived the bars').toBe('');
	});

	it('goes back on the bars\u2019 leaving curve, the same token', () => {
		draw();
		fills(true);
		frameAfter(320);
		fills(false);
		expect(drawnAt(PAGE)).toEqual(SCREEN);
		frameAfter(160);
		const halfway = bezier([0.4, 0, 1, 1])(0.5);
		expect(drawnAt(PAGE).top).toBeCloseTo(SCREEN.top + (PAGE.top - SCREEN.top) * halfway);
	});

	it('follows where the page puts the cells on each frame, never the box it first saw', () => {
		// A row folding on the way moves the cells under the wall; drawn from the first box, they
		// would overshoot and come back.
		draw();
		fills(true);
		standing = new DOMRect(0, 0, 1600, 900);
		frameAfter(160);
		const halfway = bezier([0.2, 0, 0, 1])(0.5);
		expect(drawnAt(standing).top).toBeCloseTo(PAGE.top * (1 - halfway));
	});

	it('moves its cells, edge by edge, one way from the first frame to the last', () => {
		const from = new DOMRect(233, 149, 448.9, 798);
		const to = new DOMRect(706.6, 70, 506.8, 901);
		let last = from;
		for (const k of [0, 0.25, 0.5, 0.75, 1]) {
			const { x, y, sx, sy } = between(from, to, k);
			const at = new DOMRect(to.left + x, to.top + y, to.width * sx, to.height * sy);
			if (k === 0) expect(at.top).toBeCloseTo(from.top);
			expect(at.left).toBeGreaterThanOrEqual(last.left);
			expect(at.top).toBeLessThanOrEqual(last.top);
			expect(at.bottom).toBeGreaterThanOrEqual(last.bottom);
			last = at;
		}
		expect(last.top).toBeCloseTo(to.top);
	});
});

/*
 * WHERE THE CHROME ANSWERS: the bands at the two edges, on every wall.
 *
 * Answering ANY movement anywhere would keep the bar up almost all the time, lying across the
 * bottom of several videos somebody is trying to watch. The band at the foot is where the bar actually is and where the hand goes to reach it.
 *
 * The wall's box is stubbed because jsdom lays nothing out: every rectangle is zero, so an
 * un-stubbed box puts every point inside both bands and the rule could not fail.
 */
describe('what brings the wall\u2019s chrome up', () => {
	const TALL = 800;

	function walled() {
		draw();
		const frame = host.querySelector('.wall') as HTMLElement;
		vi.spyOn(frame, 'getBoundingClientRect').mockReturnValue({
			top: 0,
			bottom: TALL,
			left: 0,
			right: 1200,
			width: 1200,
			height: TALL,
			x: 0,
			y: 0,
			toJSON: () => ({})
		} as DOMRect);
	}

	function pointerAt(clientY: number) {
		document.dispatchEvent(new MouseEvent('pointermove', { clientY, bubbles: true }));
		flushSync();
	}

	it('stays down for movement over the middle of the wall', () => {
		walled();
		pointerAt(TALL / 2);
		expect(wallChrome.up, 'the bar came up for a mouse moved over the pictures').toBe(false);
	});

	it('comes up in the band at the foot, where the bar is', () => {
		walled();
		pointerAt(TALL - 20);
		expect(wallChrome.up, 'the bar did not answer the band it sits in').toBe(true);
	});

	/* Below the wall entirely is inside the band too: the wall stops short of the window by the
	   page's own padding, and the handle drawn down there is on the SCREEN rather than on the wall. */
	it('comes up below the wall as well as inside it', () => {
		walled();
		pointerAt(TALL + 40);
		expect(wallChrome.up).toBe(true);
	});

	/* The top band stays, and it is not decoration: the shell's own bar lives up there and follows
	   this same answer, so a wall that only answered the foot would hide the top bar out of reach. */
	it('still answers the top, which is where the rest of the chrome is', () => {
		walled();
		pointerAt(20);
		expect(wallChrome.up).toBe(true);
	});
});

/*
 * THE KEYBOARD IS SOMEBODY BEING THERE: Tab raises the bars and enters them, they hold while the
 * keyboard is in them, and `B` sends them away or brings them back.
 */
describe('the keyboard and the wall\u2019s chrome', () => {
	beforeEach(() => vi.useFakeTimers());
	afterEach(() => vi.useRealTimers());

	function press(key: string) {
		document.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
		flushSync();
	}

	function bar() {
		return host.querySelector('.stage-bar') as HTMLElement;
	}

	it('raises the bars on Tab, reachable at once', () => {
		draw();
		expect(bar().inert).toBe(true);
		press('Tab');
		expect(wallChrome.up, 'Tab left the bars away').toBe(true);
		expect(bar().inert, 'the bar came up out of reach of the Tab').toBeFalsy();
	});

	it('holds them while the keyboard is in the bar, and lets them go once it is back on the wall', async () => {
		draw();
		press('Tab');
		bar().querySelector('button')!.focus();
		// Focus is heard after the update it may land in (`focusMoved`).
		await Promise.resolve();
		flushSync();
		vi.advanceTimersByTime(5000);
		flushSync();
		expect(wallChrome.up, 'the bars went from under the keyboard').toBe(true);
		(host.querySelector('.cell') as HTMLElement).dispatchEvent(
			new FocusEvent('focusin', { bubbles: true })
		);
		await Promise.resolve();
		vi.advanceTimersByTime(1600);
		flushSync();
		expect(wallChrome.up, 'the bars stayed after the keyboard left them').toBe(false);
	});

	it('leaves F to the screen change, which raises them once it lands', () => {
		// Raised at the press, the band would still be easing in as the screen filled.
		draw();
		press('f');
		expect(wallChrome.up).toBe(false);
	});

	it('brings them back on B when they are away, and sends them away on B when they are up', () => {
		draw();
		press('b');
		expect(wallChrome.up, 'B did not bring the bars back').toBe(true);
		press('b');
		expect(wallChrome.up, 'B did not send the bars away').toBe(false);
	});
});

/*
 * The bar's band: held under the pictures while the bar is up, by the strip where there is one and
 * by the grid where there is not, so the gap above the bar is the top's 8px either way. jsdom lays
 * nothing out, so the classes and the sums are pinned and `e2e/theater.spec.ts` measures the pixels.
 */
describe('the band the bar sits in', () => {
	function gridOf() {
		return host.querySelector('.grid') as HTMLElement;
	}

	/* jsdom measures every box at nought, so a pointer at y=0 is inside the top band. */
	function reachTheTop() {
		document.dispatchEvent(new MouseEvent('pointermove', { clientY: 0 }));
		flushSync();
	}

	it('is the bar, its float and the top gap again', () => {
		draw();
		const frame = host.querySelector('.wall') as HTMLElement;
		expect(frame.style.getPropertyValue('--band')).toBe(`${16 + 8}px`);
	});

	it('is held by the grid while the bar is up, and given back while it is away', () => {
		draw();
		expect(gridOf().classList.contains('held'), 'the band stayed with the bar away').toBe(false);
		reachTheTop();
		expect(gridOf().classList.contains('held'), 'the pictures ran under the bar').toBe(true);
	});

	it('is held by the strip where there is one', () => {
		const wall = draw();
		wall.strip = 2;
		flushSync();
		reachTheTop();
		expect(gridOf().classList.contains('held')).toBe(false);
		expect(
			host.querySelector('.strip.clear'),
			'the strip stopped holding the band open'
		).toBeTruthy();
	});

	it('arrives and leaves on the bars\u2019 own token and curves, so a cell never dips', () => {
		const source = readFileSync(
			join(dirname(fileURLToPath(import.meta.url)), 'TheaterWall.svelte'),
			'utf8'
		).replace(/\t/g, '');
		expect(source).toContain(
			'.grid.held {\nmargin-block-end: var(--band);\ntransition: margin-block-end var(--dur-slow) var(--ease);\n}'
		);
		expect(source, 'the band was given back on a curve the top bar does not leave on').toContain(
			'gap: 8px;\ntransition: margin-block-end var(--dur-slow) var(--ease-in);\n}'
		);
		expect(source).toContain('.strip.clear {\n--strip-room: var(--band);\n}');
		expect(source, 'the room eased under the cells while the wall moved them').toContain(
			'.wall.following .grid,\n.wall.following .strip {\ntransition: none;\n}'
		);
	});
});

/*
 * The strip's height is worked out (`stripHeight`) and written on it; unmeasured, it is the least.
 */
describe('the strip\u2019s height', () => {
	it('is written on the strip, at its least before the wall is measured', () => {
		const wall = draw();
		wall.strip = 2;
		flushSync();
		const least = Math.floor(Math.min(148, Math.max(72, window.innerHeight * 0.16)));
		const strip = host.querySelector('.strip') as HTMLElement;
		expect(strip.style.getPropertyValue('--strip-tall')).toBe(`${least}px`);
	});
});

/*
 * The strip grows down into the band while the chrome is hidden, and returns when the bars appear.
 * The band under the strip is the bar's; with the bar gone it is added to the strip's own height,
 * and the sum is the same in both states so the grid above never moves. jsdom lays nothing out, so
 * the class and the rule are held here and `e2e/theater.spec.ts` measures the pixels.
 */
describe('the strip and the band under it', () => {
	function stripOf() {
		return host.querySelector('.strip') as HTMLElement;
	}

	/** A hand at the top edge of the wall, which is what brings the chrome up. jsdom measures every
	 *  box at nought, so a pointer at y=0 is inside the top band. */
	function reachTheTop() {
		document.dispatchEvent(new MouseEvent('pointermove', { clientY: 0 }));
		flushSync();
	}

	it('spreads while the chrome is hidden and draws back when it comes up', () => {
		const wall = draw();
		wall.strip = 2;
		flushSync();
		expect(stripOf().classList.contains('spread'), 'the strip left the band empty').toBe(true);

		reachTheTop();
		expect(
			stripOf().classList.contains('spread'),
			'the strip stayed spread under a bar that is up'
		).toBe(false);
	});

	it('hands the band over as one box, so the grid above it never moves', () => {
		/* The spread height is the resting height PLUS the band, and the margin goes to nothing:
		   the same total, on the same token and curve, so the strip's top edge holds still. */
		const here = dirname(fileURLToPath(import.meta.url));
		const source = readFileSync(join(here, 'TheaterWall.svelte'), 'utf8').replace(/\t/g, '');
		expect(source).toContain(
			'.strip.clear.spread {\nblock-size: calc(var(--strip-tall) + var(--strip-room));\nmargin-block-end: 0px;\n}'
		);
		expect(source).toContain(
			'margin-block-end var(--dur-slow) var(--ease),\nblock-size var(--dur-slow) var(--ease);'
		);
	});
});

/*
 * A FEED TAKEN OFF THE WALL LEAVES THE OTHERS' PICTURES ALONE.
 *
 * The model half of this is pinned in `wall.test.ts`: the cells move rather than their settings
 * being copied along. This is the half only a DOM can answer: each cell is drawn under its own key,
 * so moving the object moves the ELEMENT with it rather than building a new one. A rebuilt `<video>`
 * has no source, no buffer and no playhead, and starts all over again from the beginning.
 */
describe('closing one feed', () => {
	/** A file, as a cell knows one. Enough for the cell to draw a picture for it. */
	function file(id: string) {
		return {
			id,
			media_type: 'video',
			duration_ms: 1000,
			thumb: false,
			art: null,
			original_filename: null,
			favorite: false,
			rating: null,
			concealed: false,
			width: 1920,
			height: 1080
		};
	}

	const plan: PlaybackPlan = {
		route: 'direct',
		streamable: true,
		url: '/stream',
		reason: '',
		scale_height: null,
		resume_ms: null,
		copy_kind: null,
		duration_ms: 1000,
		projected_realtime: null,
		qualities: [],
		view_at_ms: 0
	};

	it('leaves the other feed on the very element it was already playing in', () => {
		const wall = draw();
		wall.setLayout('side_by_side');
		for (const [at, cell] of wall.cells.entries()) {
			cell.playing = file(`file-${at}`);
			cell.plan = plan;
			cell.state = 'ready';
		}
		flushSync();
		const before = [...host.querySelectorAll('video')];
		expect(before, 'the wall drew no pictures to move').toHaveLength(2);

		wall.remove(0);
		flushSync();

		const after = [...host.querySelectorAll('video')];
		expect(after).toHaveLength(1);
		expect(after[0], 'the surviving feed was built again from nothing').toBe(before[1]);
	});
});

/*
 * The leftover is shared. The wall is aspect-true, so at most sizes it cannot use the whole box;
 * putting the whole remainder above the pictures would read as the cells having slid down the
 * screen, so it is centred. Pinned here because the stylesheet is the only place it exists and a
 * one-word edit would put it wrong.
 *
 * Read off the component's source: the rule is scoped, and asking the document would test whether
 * this runner injects styles rather than what the component says.
 */
describe('where the wall puts what it cannot use', () => {
	const source = readFileSync(
		join(dirname(fileURLToPath(import.meta.url)), 'TheaterWall.svelte'),
		'utf8'
	);

	it('centres the leftover, in every case', () => {
		/*
		 * Declarations only: the match is anchored to a line that is a declaration and nothing
		 * else, so a rule named in a comment cannot satisfy it.
		 */
		const declared = [...source.matchAll(/^\s*align-content:\s*([a-z]+);\s*$/gm)].map(
			(one) => one[1]
		);

		expect(declared.length).toBeGreaterThan(0);
		expect(new Set(declared)).toEqual(new Set(['center']));
	});
});
