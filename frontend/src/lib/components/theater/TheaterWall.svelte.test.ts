/* THE WALL SETTLES WHEN THE SCREEN FILLS, AND AGAIN WHEN IT LETS GO.
 *
 * Entering full screen is the biggest change this application makes: every picture on the wall
 * changes size at once. The chrome has motion on both edges of that, and pictures with none
 * would cut while the bars slide.
 *
 * What is pinned here is the RULE rather than the pixels: the first draw is not a change of state
 * and must not animate, every change after it must, and it is the movement every player's screen
 * change makes (`screenChanges` in `player/motion.ts`): the wall eases from the box it stood in,
 * at the stage's pace going up and one pace quicker coming down. jsdom has no layout and no
 * compositor, so the wall's box is said here and the animation is recorded rather than watched.
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(async () => ({ items: [], total: 0 })), post: vi.fn(async () => ({})) }
}));

import TheaterWall from './TheaterWall.svelte';
import { Wall } from '$lib/theater/wall.svelte';
import type { PlaybackPlan } from '$lib/player/playback';
import { stage } from '$lib/components/shell/stage.svelte';
import { wallChrome } from '$lib/theater/chrome.svelte';

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
/** Where the wall stands: in the page, or filling the screen. */
let standing = PAGE;
const animate = vi.fn();

/** How the screen says it has been filled, or let go: the browser first, then the shell. */
function fills(yes: boolean) {
	standing = yes ? SCREEN : PAGE;
	stage.filling = yes;
	document.dispatchEvent(new Event('fullscreenchange'));
	flushSync();
}

/** The calls that moved the wall itself, rather than anything inside it. */
function wallMoves() {
	return animate.mock.contexts
		.map((element, at) => ({ element: element as HTMLElement, call: animate.mock.calls[at] }))
		.filter(({ element }) => element.classList.contains('wall'));
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

/** The wall's box said, and its movements recorded: for the settling tests only. */
function watchTheWall() {
	animate.mockClear();
	standing = PAGE;
	vi.stubGlobal('requestAnimationFrame', (run: FrameRequestCallback) => {
		run(0);
		return 0;
	});
	vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
		this: HTMLElement
	) {
		return this.classList.contains('wall') ? standing : new DOMRect(0, 0, 0, 0);
	});
	HTMLElement.prototype.animate = animate as unknown as HTMLElement['animate'];
	HTMLElement.prototype.getAnimations = () => [];
}

describe('the wall settling around a change of screen', () => {
	beforeEach(watchTheWall);
	afterEach(() => {
		vi.unstubAllGlobals();
		delete (HTMLElement.prototype as Partial<HTMLElement>).animate;
		delete (HTMLElement.prototype as Partial<HTMLElement>).getAnimations;
	});

	it('does not animate on the first draw', () => {
		// Opening Theater is not a change of state, and a wall that fades in on arrival is a screen
		// that looks slow to load.
		draw();
		expect(wallMoves(), 'the wall animated itself into existence').toHaveLength(0);
	});

	it('settles when the screen fills', () => {
		draw();
		fills(true);
		expect(wallMoves(), 'the wall cut straight from one size to the other').toHaveLength(1);
	});

	it('settles again when the screen lets go', () => {
		// The half a stylesheet cannot do: the element stops matching `:fullscreen` the instant the
		// browser lets go, so there is no state left to animate from.
		draw();
		fills(true);
		animate.mockClear();
		fills(false);
		expect(wallMoves(), 'leaving full screen was not answered at all').toHaveLength(1);
	});

	it('grows from the box it stood in at the stage pace, and goes back one pace quicker', () => {
		draw();
		fills(true);
		const [[frames, options]] = wallMoves().map(({ call }) => call);
		expect(frames[0]).toMatchObject({ offset: 0, scale: `${PAGE.width / SCREEN.width}` });
		// The stylesheet is not loaded here, so these are the values the stage tokens stand for.
		expect(options.duration).toBe(320);
		animate.mockClear();
		fills(false);
		expect(wallMoves()[0].call[1].duration).toBe(200);
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
 * The pictures take the whole height; the bar rises over them.
 *
 * The grid reserves no band for the bar, which fades with the chrome while staying mounted, so a
 * reserved band would be held open with nothing in it. The strip, a row of controls at the edge the
 * bar rises from, still keeps its margin.
 *
 * The class is pinned rather than the pixels: jsdom lays nothing out, so the bottom edge is
 * measured in `e2e/theater.spec.ts` and the condition is what a test can hold.
 */
describe('the band the bar sits in', () => {
	function gridOf() {
		return host.querySelector('.grid') as HTMLElement;
	}

	it('is never taken out of the grid, with the chrome up or down', () => {
		draw();
		expect(gridOf().classList.contains('clear'), 'the grid reserved room for a bar').toBe(false);
	});

	it('is still held open by the strip where there is one', () => {
		const wall = draw();
		wall.strip = 2;
		flushSync();
		expect(gridOf().classList.contains('clear')).toBe(false);
		expect(
			host.querySelector('.strip.clear'),
			'the strip stopped holding the band open'
		).toBeTruthy();
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

	it('does not take the bar band out of the box it lays the wall out in', () => {
		/* Declarations only, for the reason above. */
		expect(source).not.toMatch(/^\s*\.grid\.clear\s*\{/m);
		expect(source).not.toMatch(
			/^\s*margin-block-end: calc\(var\(--bar-room, 0px\) \+ var\(--space-4\)\);/m
		);
	});
});
