/* A cell letting go of its stream, which is the one promise on this screen with a real budget behind
 * it.
 *
 * Four long-lived media streams leave two connections for everything else the same origin serves,
 * and a browser keeps the socket for an element that has merely been hidden. So a cell that stops
 * showing a video has to say so, and the case that is easy to miss is a PHOTOGRAPH, because a
 * still replaces the video element rather than changing its source. The picture goes, and without
 * this the stream behind it does not.
 *
 * It is tested by mounting, because it is a fact about the component's effects rather than about the
 * cell: the cell has already moved on, correctly, in every one of these.
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const detach = vi.fn();
/** Every time the element is handed a plan: a new file, or a stalled one asked for again. */
const attached = vi.fn();

vi.mock('$lib/player/playback', async () => {
	const real = await vi.importActual<typeof import('$lib/player/playback')>('$lib/player/playback');
	return {
		...real,
		attach: () => {
			attached();
			return { detach };
		}
	};
});

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(async () => ({ items: [], total: 0 })), post: vi.fn(async () => ({})) }
}));

import CellView from './CellView.svelte';
import { api } from '$lib/api/client';
import { Cell, type Playable } from '$lib/theater/cell.svelte';
import { Wall } from '$lib/theater/wall.svelte';
import { fileFacts } from '$lib/design/testing.svelte';
import { ASSUMED } from '$lib/theater/fit';
import { STALL_MS, STALLED_WORDS } from '$lib/theater/stall';
import { REPAIRED_WORDS } from '$lib/player/facts';

/** A plan, as the server answers for a file that plays as it is. */
const PLAN = {
	route: 'direct' as const,
	reason: '',
	copy_kind: null,
	url: '/api/assets/a/stream',
	scale_height: null,
	projected_realtime: null,
	streamable: true,
	duration_ms: 1000,
	resume_ms: null,
	view_at_ms: 2000,
	qualities: [],
	unreadable: null,
	scan_queued: false
};

function playable(id: string, kind: string): Playable {
	return {
		id,
		media_type: kind,
		duration_ms: 1000,
		thumb: true,
		width: 1920,
		height: 1080,
		art: null,
		original_filename: null,
		favorite: false,
		rating: null,
		concealed: false
	};
}

let host: HTMLElement;
let running: Record<string, unknown>;

/** A cell already showing something, drawn into the document. */
function show(kind: string) {
	const wall = new Wall();
	const cell = wall.all[0];
	cell.playing = playable('a', kind);
	cell.plan = PLAN;
	cell.state = 'ready';

	host = document.createElement('div');
	document.body.append(host);
	running = mount(CellView, {
		target: host,
		props: {
			wall,
			cell,
			index: 0,
			marksUp: true,
			chromeUp: true,
			numbered: true,
			onpick: () => {},
			onfullscreen: () => {},
			place: '1 / 1 / span 1 / span 1'
		}
	});
	flushSync();
	return { wall, cell };
}

beforeEach(() => {
	detach.mockClear();
	attached.mockClear();
	// A single press waits out the double-click interval before it acts, so these run on a clock the
	// test moves rather than on the wall one.
	vi.useFakeTimers();
	// jsdom implements `play` as a stub that returns nothing, and the component treats the promise
	// every real engine hands back as the thing to catch a refusal on.
	vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
});

afterEach(() => {
	if (running) void unmount(running);
	host?.remove();
	vi.useRealTimers();
});

describe('letting go of the stream', () => {
	it('lets go when the cell moves to a photograph', () => {
		/* The leak. A still has no video element, so an effect that returned early when there was
		 * none would never detach what came before it: the picture gone and the socket not. */
		const { cell } = show('video');
		expect(host.querySelector('video')).not.toBeNull();

		cell.playing = playable('b', 'image');
		flushSync();

		expect(host.querySelector('video'), 'the video element is still there').toBeNull();
		expect(detach, 'the stream behind a replaced picture was never released').toHaveBeenCalled();
	});

	it('lets go when the cell moves to another video', () => {
		const { cell } = show('video');

		cell.playing = playable('b', 'video');
		flushSync();

		expect(detach).toHaveBeenCalled();
	});

	it('lets go when the cell is emptied', () => {
		const { cell } = show('video');

		cell.release();
		flushSync();

		expect(detach).toHaveBeenCalled();
	});
});

describe('what the element is told', () => {
	it("is handed every value Sift decides, rather than the browser's own answers", () => {
		show('video');
		const video = host.querySelector('video') as HTMLVideoElement;

		expect(video.loop, 'the element was left to decide whether to repeat').toBe(false);
		expect(video.autoplay, 'the element was left to decide when to start').toBe(false);
		expect(video.muted, 'a cell was not silent to begin with').toBe(true);
		expect(video.preload).toBe('none');
	});
});

describe('pressing the picture', () => {
	/*
	 * A press chooses this cell and does nothing else; a double fills the screen, having chosen it.
	 *
	 * Every control on the screen acts on the chosen cell (the bar at the foot of a filled wall,
	 * the filter panel, the number keys, the mute), and pressing the picture is the obvious way to
	 * say which one is meant, so it must not do something else instead, such as play and pause.
	 *
	 * There is no delay separating a press from a double: choosing twice is choosing, so the two do
	 * not fight and there is nothing to disambiguate. A cell lights the instant it is pressed.
	 */
	function press(video: HTMLElement, times: number) {
		// `bubbles` because Svelte delegates the common events at the root of the app: an event that
		// does not bubble reaches nothing, and the test passes against a component doing nothing.
		video.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: times }));
		if (times > 1) video.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
	}

	it('chooses this cell, and leaves playback exactly as it was', () => {
		const { wall, cell } = show('video');
		wall.focused = 1;
		const before = { wall: wall.paused, cell: cell.paused };
		const video = host.querySelector('video') as HTMLVideoElement;

		press(video, 1);
		flushSync();

		expect(wall.focused, 'a press did not choose the cell it landed on').toBe(0);
		expect(
			{ wall: wall.paused, cell: cell.paused },
			'a press on a cell changed what was playing'
		).toEqual(before);
	});

	it('chooses it with no delay at all', () => {
		// Nothing is scheduled, so the answer is there before any double-click timer could have
		// run.
		const { wall } = show('video');
		wall.focused = 1;
		const video = host.querySelector('video') as HTMLVideoElement;

		press(video, 1);
		flushSync();

		expect(wall.focused).toBe(0);
	});

	it('fills the screen on two, and leaves playback where it was', () => {
		let filled = 0;
		const wall = new Wall();
		const cell = wall.all[0];
		cell.playing = playable('a', 'video');
		cell.plan = PLAN;
		cell.state = 'ready';
		host = document.createElement('div');
		document.body.append(host);
		running = mount(CellView, {
			target: host,
			props: {
				wall,
				cell,
				index: 0,
				marksUp: true,
				chromeUp: true,
				numbered: true,
				onpick: () => {},
				onfullscreen: () => (filled += 1),
				place: '1 / 1 / span 1 / span 1'
			}
		});
		flushSync();

		const video = host.querySelector('video') as HTMLVideoElement;
		const before = { wall: wall.paused, cell: cell.paused };

		press(video, 2);
		vi.advanceTimersByTime(400);
		flushSync();

		expect(filled, 'two presses did not fill the screen').toBe(1);
		expect(
			{ wall: wall.paused, cell: cell.paused },
			'filling the screen changed what was playing'
		).toEqual(before);
	});
});

describe('pressing a preview', () => {
	/* A preview is chosen like any cell on one press; only a double press brings it up, once. */
	function preview() {
		const wall = new Wall();
		wall.setLayout('center_stage');
		const index = wall.inFocus.length;
		const cell = wall.at(index) as Cell;
		cell.playing = playable('p', 'video');
		cell.plan = PLAN;
		cell.state = 'ready';
		const brought = vi.fn();
		host = document.createElement('div');
		document.body.append(host);
		running = mount(CellView, {
			target: host,
			props: {
				wall,
				cell,
				index,
				marksUp: true,
				chromeUp: true,
				numbered: true,
				onpick: () => {},
				onfullscreen: () => {},
				preview: true,
				onpress: brought
			}
		});
		flushSync();
		return { wall, index, brought, video: host.querySelector('video') as HTMLVideoElement };
	}

	it('chooses it on one press and brings nothing up', () => {
		const { wall, index, brought, video } = preview();

		video.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }));
		flushSync();

		expect(wall.focused).toBe(index);
		expect(brought, 'one press brought the preview up').not.toHaveBeenCalled();
	});

	it('brings it up once on a double press', () => {
		const { brought, video } = preview();

		video.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }));
		video.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 2 }));
		video.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
		flushSync();

		expect(brought, 'a double-click swapped it up and straight back').toHaveBeenCalledTimes(1);
	});
});

describe('the shape the player measured', () => {
	it('is written onto the cell, which draws itself from it', () => {
		const { cell } = show('video');
		const video = host.querySelector('video') as HTMLVideoElement;
		Object.defineProperty(video, 'videoWidth', { value: 1440 });
		Object.defineProperty(video, 'videoHeight', { value: 1920 });

		video.dispatchEvent(new Event('loadedmetadata'));
		flushSync();

		expect(cell.measured).toEqual({ file: 'a', width: 1440, height: 1920 });
		const section = host.querySelector('section.cell') as HTMLElement;
		expect(section.style.getPropertyValue('--shape')).toBe(`${1440 / 1920}`);
	});
});

describe('the facts panel', () => {
	/* Its whole point is that it is not a panel of its own: a cell draws the PLAYER's panel, over
	 * the picture, in the same corner, saying the same things in the same words. What is checked
	 * here is the wiring: that the control opens it, that it is the shared component and not a
	 * second copy, and that the lines about the file are filled from what the cell fetched. The
	 * sentences themselves are checked in `facts.test.ts`. */

	/** The lines the player shows, in the order it shows them. */
	const SHARED = [
		'Aspect ratio',
		'Bit depth',
		'Bitrate',
		'Buffered ahead',
		'Codec',
		'Container',
		'Dimensions',
		'Frame rate',
		'Length',
		'Playback',
		'Playing at',
		'Position',
		'Size on disk'
	];

	/*
	 * Opened by asking the cell, which is where the answer lives.
	 *
	 * A cell has no bar of its own (there is one for the whole wall), so the control and the panel
	 * are in two different components, and the state that joins them is on the cell. This tests the
	 * half this component owns: given the answer, does it draw the player's panel. Whether the
	 * control on the bar writes that answer is the bar's to prove.
	 *
	 * It also catches a piece of state kept in each component, which would light the button on the
	 * bar while the panel never appeared.
	 */
	function open(cell: Cell) {
		cell.factsOpen = true;
		flushSync();
		return host.querySelector('[aria-label="Stats for nerds, cell 1"]') as HTMLElement;
	}

	function lines(panel: HTMLElement): Record<string, string> {
		const terms = [...panel.querySelectorAll('dt')].map((dt) => dt.textContent?.trim() ?? '');
		const values = [...panel.querySelectorAll('dd')].map((dd) => dd.textContent?.trim() ?? '');
		return Object.fromEntries(terms.map((term, at) => [term, values[at]]));
	}

	it('is shut until it is asked for', () => {
		show('video');
		expect(host.querySelector('[aria-label="Stats for nerds, cell 1"]')).toBeNull();
	});

	it('shows every line the player shows, in the player order', () => {
		const { cell } = show('video');
		const panel = open(cell);
		expect(panel, 'the cell asked for the panel and none was drawn').not.toBeNull();

		const terms = [...panel.querySelectorAll('dt')].map((dt) => dt.textContent?.trim());
		expect(terms.slice(0, SHARED.length)).toEqual(SHARED);
	});

	it("adds the cell's own two lines at the end, and nothing else", () => {
		const { cell } = show('video');
		cell.source = 'tag:cars';
		flushSync();

		const said = lines(open(cell));
		expect(Object.keys(said)).toEqual([...SHARED, 'Source', 'State']);
		expect(said.Source).toBe('tag:cars');
		expect(said.State).toBe('ready');
	});

	it('fills the lines about the file from what the cell fetched, in the player words', () => {
		const { cell } = show('video');
		cell.facts = {
			width: 1920,
			height: 1080,
			container: 'mkv',
			size_bytes: 12_000_000,
			vcodec: 'h264',
			acodec: 'aac',
			fps: 60,
			bit_depth: 10
		};
		cell.plan = { ...PLAN, route: 'transcode' };
		flushSync();

		const said = lines(open(cell));
		expect(said).toMatchObject({
			'Aspect ratio': '16:9',
			'Bit depth': '10-bit',
			Codec: 'H.264 / AAC',
			Container: 'mkv',
			Dimensions: '1920 x 1080',
			'Frame rate': '60 fps',
			// The whole reason the words are shared: not `transcode` said at somebody.
			Playback: 'Transcoding',
			// Twelve million bytes is 12 MB, not 11: a panel dividing by 1024 and writing `MB` would
			// be one more copy of the arithmetic the shared words hold once.
			'Size on disk': '12 MB'
		});
	});

	it('says "Unknown" rather than the last file\'s facts while the next one is arriving', () => {
		const { cell } = show('video');
		cell.facts = fileFacts({ container: 'mp4', vcodec: 'av1' });
		flushSync();
		expect(lines(open(cell)).Container).toBe('mp4');

		cell.facts = null;
		flushSync();
		const panel = host.querySelector('[aria-label="Stats for nerds, cell 1"]') as HTMLElement;
		expect(lines(panel).Container).toBe('Unknown');
	});
});

describe('pressing a cell with nothing in it', () => {
	/*
	 * A cell that is not playing anything is still a cell, and is chosen the same way.
	 *
	 * The sentence a cell shows when it has nothing to draw is an overlay at `inset: 0`, so on a
	 * cell with no picture it is the only thing under the pointer. If it swallowed presses, the one
	 * rectangle on the wall a person most wants to press (it is showing nothing, so they want to
	 * change where it draws from) would be the one that could not be chosen at all.
	 *
	 * Every state that draws the overlay is covered, not only `nothing_here`: the rule is about the
	 * overlay itself.
	 */
	function nothing(state: 'nothing_here' | 'empty' | 'stopped' | 'failed') {
		const wall = new Wall();
		const cell = wall.all[0];
		cell.playing = null;
		cell.state = state;

		host = document.createElement('div');
		document.body.append(host);
		running = mount(CellView, {
			target: host,
			props: {
				wall,
				cell,
				index: 0,
				marksUp: true,
				chromeUp: true,
				numbered: true,
				onpick: () => {},
				onfullscreen: () => {},
				place: '1 / 1 / span 1 / span 1'
			}
		});
		flushSync();
		return { wall, cell };
	}

	/** The overlay's own control. */
	function sentence(): HTMLElement {
		const button = host.querySelector('button.say-press');
		if (!button) throw new Error('the cell drew no pressable overlay');
		return button as HTMLElement;
	}

	it('chooses this cell when the words on it are pressed', () => {
		const { wall } = nothing('nothing_here');
		wall.focused = 1;

		sentence().dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }));
		flushSync();

		expect(wall.focused, 'a press on a cell with nothing in it chose nothing').toBe(0);
	});

	it('fills the screen on two, having chosen it', () => {
		let filled = 0;
		const wall = new Wall();
		const cell = wall.all[0];
		cell.playing = null;
		cell.state = 'nothing_here';
		host = document.createElement('div');
		document.body.append(host);
		running = mount(CellView, {
			target: host,
			props: {
				wall,
				cell,
				index: 0,
				marksUp: true,
				chromeUp: true,
				numbered: true,
				onpick: () => {},
				onfullscreen: () => (filled += 1),
				place: '1 / 1 / span 1 / span 1'
			}
		});
		flushSync();
		wall.focused = 1;

		const button = sentence();
		button.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 2 }));
		button.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
		flushSync();

		expect(wall.focused).toBe(0);
		expect(filled, 'a double press on an empty cell did not fill the screen').toBe(1);
	});

	it('gives the cell the shape the WALL assumes, so it has a width to be pressed in', () => {
		/*
		 * A cell with no picture must not be drawn zero pixels wide, or the press has nothing to
		 * land on: everything inside it is absolutely positioned, so `inline-size: auto` has no
		 * content to measure. `ASSUMED` is what `fit.ts` uses for a cell that cannot say its shape,
		 * so what is pinned is the two halves agreeing rather than the number.
		 */
		nothing('nothing_here');
		const cell = host.querySelector('section.cell') as HTMLElement;
		expect(cell.style.getPropertyValue('--shape'), 'an empty cell was left with no shape').toBe(
			`${ASSUMED}`
		);
	});

	it("says Sift can't reach the files, not that the setting matched nothing, when that is why", () => {
		const { cell } = nothing('nothing_here');
		expect(sentence().textContent).toContain('Nothing here matches what this cell is set to.');

		cell.unreachable = true;
		flushSync();
		expect(sentence().textContent?.replace(/\s+/g, ' ')).toContain(
			"Sift can't reach the files this cell found. A drive may not be mounted, or a folder may have moved."
		);
	});

	it('is a real button, so the keyboard reaches it', () => {
		// Why it is a button rather than a click handler on the words: a wall driven by the keys has
		// to be able to reach a cell showing nothing, and only a focusable control can be reached.
		nothing('nothing_here');
		expect(sentence().tagName).toBe('BUTTON');
	});

	it.each(['empty', 'stopped', 'failed'] as const)(
		'is pressable while the cell says %s, not only when it has run out',
		(state) => {
			const { wall } = nothing(state);
			wall.focused = 1;

			sentence().dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }));
			flushSync();

			expect(wall.focused).toBe(0);
		}
	);
});

describe('the mark saying this cell is being converted', () => {
	/* A wall is four to nine pictures together, and converting one of them costs the machine
	 * something for every second it plays. Which cell is doing it is the fact worth having per cell:
	 * four at the same time is why the whole wall struggles, and without the mark nothing on screen says which.
	 *
	 * The mark is deliberately NOT drawn for every file with something to explain. It is drawn for
	 * the one path that is doing work right now. */

	function mark() {
		return host.querySelector('[aria-label^="Why this file"], [aria-label^="Why skipping"]');
	}

	it('is there while this cell is converting', () => {
		const { cell } = show('video');
		cell.plan = { ...PLAN, route: 'transcode', reason: 'Your browser cannot play H265.' };
		flushSync();

		expect(mark()).not.toBeNull();
	});

	it('says what the SERVER said, not a sentence of its own', () => {
		// One decision, one explanation. A sentence written in the cell would be free to drift from
		// the one the facts panel shows two lines below it.
		const { cell } = show('video');
		cell.plan = { ...PLAN, route: 'transcode', reason: 'Your browser cannot play H265.' };
		flushSync();

		expect(host.textContent).toContain('Your browser cannot play H265.');
	});

	it('is absent on a cell playing the file as it is', () => {
		const { cell } = show('video');
		cell.plan = { ...PLAN, route: 'direct' };
		flushSync();

		expect(mark()).toBeNull();
	});

	it("says so on a cell playing a repackaged copy, in the server's words", () => {
		/*
		 * The rule is one for every picture: any path that is not direct play says why, and the
		 * mark is quiet enough to say it.
		 */
		const { cell } = show('video');
		cell.plan = {
			...PLAN,
			route: 'remux',
			reason: 'Your browser cannot read this container, so you are watching a repackaged copy.'
		};
		cell.repair = null;
		flushSync();

		expect(mark()).not.toBeNull();
		expect(host.textContent).toContain('watching a repackaged copy');
	});

	it('IS there for a file whose sound is stored far from its picture', () => {
		/*
		 * A corrected copy costs nothing per second, but Sift is doing something about this
		 * particular file, and the wall says so. Rare enough to be a mark rather than noise.
		 */
		const { cell } = show('video');
		cell.plan = {
			...PLAN,
			route: 'remux',
			// The server's own sentence for a repaired copy (`_COPY_REASON` in the player's policy).
			reason: REPAIRED_WORDS
		};
		cell.repair = 'repaired';
		flushSync();

		expect(mark()).not.toBeNull();
		expect(host.textContent).toContain('Playing a repackaged copy');
	});

	it('says the copy is still being MADE while it is', () => {
		const { cell } = show('video');
		cell.repair = 'pending';
		flushSync();

		expect(host.textContent).toContain('Sift finishes a repackaged copy');
	});

	it('says the repair is switched off, without a link a wall cannot follow', () => {
		const { cell } = show('video');
		cell.repair = 'off';
		flushSync();

		expect(host.textContent).toContain("that's switched off");
		expect(host.textContent).not.toContain('Turn it on');
	});

	it('lets a CONVERSION win over the file, because it is what is happening now', () => {
		const { cell } = show('video');
		cell.plan = { ...PLAN, route: 'transcode', reason: 'Your browser cannot play H265.' };
		cell.repair = 'repaired';
		flushSync();

		expect(host.textContent).toContain('Your browser cannot play H265.');
	});

	it('is absent on a photograph', () => {
		const { cell } = show('image');
		cell.plan = { ...PLAN, route: 'transcode' };
		cell.repair = 'repaired';
		flushSync();

		expect(mark()).toBeNull();
	});

	it('is absent on an empty cell', () => {
		const { cell } = show('video');
		cell.plan = { ...PLAN, route: 'transcode' };
		cell.playing = null;
		flushSync();

		expect(mark()).toBeNull();
	});
});

describe('handing the wall between hosts', () => {
	/* Sending the wall to the corner panel unmounts the cells on the screen and mounts new ones in
	   the panel, and the way back does the same in reverse, so without this every clip would start
	   from the beginning both ways. A cell's position is the cell's: written back as the element goes, picked up by
	   the next element the moment it knows how long the file is. */
	it('comes back where it was: a handoff at 12 s resumes at 12 s', () => {
		const { wall, cell } = show('video');
		const first = host.querySelector('video') as HTMLVideoElement;
		first.currentTime = 12;
		// The element says where it is on every tick, and the cell writes it down; the handoff reads
		// the cell rather than an element that is already going.
		first.dispatchEvent(new Event('timeupdate'));

		void unmount(running);
		expect(cell.resumeAt).toBe(12);

		host = document.createElement('div');
		document.body.append(host);
		running = mount(CellView, {
			target: host,
			props: {
				wall,
				cell,
				index: 0,
				marksUp: true,
				chromeUp: true,
				numbered: true,
				onpick: () => {},
				onfullscreen: () => {},
				place: '1 / 1 / span 1 / span 1'
			}
		});
		flushSync();
		const second = host.querySelector('video') as HTMLVideoElement;
		second.dispatchEvent(new Event('loadedmetadata'));

		expect(second.currentTime).toBe(12);
		expect(cell.resumeAt).toBeNull();
	});

	it('writes nothing back for a clip that had not started', () => {
		const { cell } = show('video');

		void unmount(running);

		expect(cell.resumeAt).toBeNull();
	});
});

describe('what a cell records about the picture it showed', () => {
	/*
	 * A PHOTOGRAPH AND A GIF ARE SITTINGS TOO. Timing begun on the video element's `playing` event
	 * alone would miss them, since a picture is drawn in an `<img>` that never fires it, so every
	 * picture and every GIF a wall showed would be shown to nobody as far as the record went, and
	 * that cannot be filled in afterwards.
	 */
	function sittings() {
		return vi
			.mocked(api.post)
			.mock.calls.filter((call) => String(call[0]).endsWith('/view'))
			.map((call) => (call[1] as { body: Record<string, unknown> }).body);
	}

	for (const [kind, called] of [
		['image', 'a photograph'],
		['gif', 'a GIF']
	]) {
		it(`records the time ${called} was on screen, in Theater`, () => {
			vi.mocked(api.post).mockClear();
			let clock = 1_000;
			vi.spyOn(performance, 'now').mockImplementation(() => clock);
			const { wall } = show(kind);
			wall.paused = false;
			flushSync();

			(host.querySelector('img.picture') as HTMLImageElement).dispatchEvent(new Event('load'));
			flushSync();
			clock += 5_000;
			void unmount(running);
			running = undefined as unknown as Record<string, unknown>;

			expect(sittings()).toHaveLength(1);
			expect(sittings()[0]).toMatchObject({ watch_ms: 5_000, screen: 'theater' });
		});
	}

	it('does not count the time a held wall sat with a picture on it', () => {
		/* A picture loads whether or not the wall is stopped, and a wall OPENS stopped. */
		vi.mocked(api.post).mockClear();
		let clock = 1_000;
		vi.spyOn(performance, 'now').mockImplementation(() => clock);
		const { wall } = show('image');
		expect(wall.paused).toBe(true);

		(host.querySelector('img.picture') as HTMLImageElement).dispatchEvent(new Event('load'));
		flushSync();
		clock += 60_000;
		void unmount(running);
		running = undefined as unknown as Record<string, unknown>;

		expect(sittings(), 'an hour of a held wall is not an hour of anybody looking').toEqual([]);
	});
});

describe('holding a GIF this browser cannot drive', () => {
	/*
	 * A wall opens held, so a GIF always arrives into a cell that is already stopped.
	 *
	 * The frame is copied onto a canvas and the canvas shown in its place, the only thing a page
	 * can do about an animating `<img>`. What it needs is a picture with a size on it, and there is
	 * none until the image has decoded; `naturalWidth` is not something the component watches, so
	 * the copy has to run again when the picture arrives, or every GIF on a freshly opened wall
	 * would animate on a wall that says it is stopped.
	 *
	 * jsdom never loads an image and never draws a canvas, so the size is stubbed and the event
	 * dispatched by hand: what is under test is the effect running again, not the decoding.
	 */
	function pictureArrives(image: HTMLImageElement, wide: number, tall: number) {
		Object.defineProperty(image, 'naturalWidth', { value: wide, configurable: true });
		Object.defineProperty(image, 'naturalHeight', { value: tall, configurable: true });
		image.dispatchEvent(new Event('load'));
		flushSync();
	}

	it('freezes one that finishes loading while the wall is already held', () => {
		const { wall } = show('gif');
		expect(wall.paused, 'a wall is supposed to open held').toBe(true);
		const image = host.querySelector('img.picture') as HTMLImageElement;
		const canvas = host.querySelector('canvas.picture') as HTMLCanvasElement;
		expect(canvas.classList.contains('hidden'), 'there is nothing to show before it loads').toBe(
			true
		);

		pictureArrives(image, 600, 800);

		expect(
			image.classList.contains('hidden'),
			'the animating picture is still the one on screen'
		).toBe(true);
		expect(canvas.classList.contains('hidden'), 'the frozen frame was never shown').toBe(false);
		expect([canvas.width, canvas.height], 'the canvas was never sized from the picture').toEqual([
			600, 800
		]);
	});

	it('freezes the next one too, at the same size as the last', () => {
		/* The count is why this is a count. A held cell stepping from one GIF to another of
		   exactly the same size moves no measurement at all, so anything watching the size would run
		   once and never again. */
		const { cell } = show('gif');
		pictureArrives(host.querySelector('img.picture') as HTMLImageElement, 600, 800);

		cell.playing = playable('b', 'gif');
		flushSync();
		const canvas = host.querySelector('canvas.picture') as HTMLCanvasElement;
		canvas.width = 1;
		canvas.height = 1;
		pictureArrives(host.querySelector('img.picture') as HTMLImageElement, 600, 800);

		expect([canvas.width, canvas.height], 'the second GIF was never copied').toEqual([600, 800]);
	});

	it('shows the picture again the moment the wall is let go', () => {
		const { wall } = show('gif');
		const image = host.querySelector('img.picture') as HTMLImageElement;
		pictureArrives(image, 600, 800);

		wall.togglePause();
		flushSync();

		expect(image.classList.contains('hidden'), 'the picture was left behind the frozen frame').toBe(
			false
		);
	});
});

/*
 * What an empty cell says, which is two different things. "Nothing chosen yet: pick what this cell
 * should play" is right for a cell somebody emptied and wrong for a cell the wall has not finished
 * choosing for, where it would read as an instruction about a wall that is busy.
 */
describe('a cell with nothing in it', () => {
	/** An empty cell, drawn into the document. */
	function empty() {
		const wall = new Wall();
		const cell = wall.all[0];

		host = document.createElement('div');
		document.body.append(host);
		running = mount(CellView, {
			target: host,
			props: {
				wall,
				cell,
				index: 0,
				marksUp: true,
				chromeUp: true,
				numbered: true,
				onpick: () => {},
				onfullscreen: () => {},
				place: '1 / 1 / span 1 / span 1'
			}
		});
		flushSync();
		return { wall, cell };
	}

	it('asks to be pointed at something when the wall is not opening', () => {
		empty();

		expect(host.textContent).toContain('Nothing chosen yet');
	});

	it('says it is looking while the wall is still opening', () => {
		const { wall } = empty();

		wall.opening = true;
		flushSync();

		expect(
			host.textContent,
			'a cell mid-opening told somebody to go and pick something'
		).not.toContain('Nothing chosen yet');
		expect(host.textContent).toContain('Finding something to play');
	});
});

/*
 * THE WAITING FILTER'S MARK SAYS WHAT IT IS, and is not a control.
 *
 * Words living only in its accessible name could not be read by somebody looking at the picture,
 * and a disc reads as a button. It says so on hover, through the shared tooltip, and it is
 * nothing anybody can press or tab to.
 */
describe('the mark of a filter waiting for the file to end', () => {
	function waiting() {
		const drawn = show('video');
		drawn.cell.narrowTo(new URLSearchParams({ q: 'holiday' }));
		flushSync();
		const mark = host.querySelector('.waits') as HTMLElement | null;
		if (mark === null) throw new Error('no mark was drawn for a waiting filter');
		return mark;
	}

	it('says what it means when the pointer rests on it', () => {
		const mark = waiting();
		const target = mark.querySelector('.wrap') as HTMLElement;
		target.dispatchEvent(new PointerEvent('pointermove', { bubbles: true }));
		vi.advanceTimersByTime(1000);
		flushSync();

		const said = document.querySelector('[role="tooltip"]');
		expect(said?.textContent ?? '', 'hovering the mark said nothing').toContain(
			'New filter starts when this file ends'
		);
	});

	it('is a status: nothing in it can be pressed or tabbed to', () => {
		const mark = waiting();
		expect(mark.querySelector('button, a, [tabindex], [role="button"]')).toBeNull();
		expect(mark.getAttribute('tabindex')).toBeNull();
	});
});

/*
 * The chosen cell's edge is drawn over the picture: an `outline` on the section would be painted
 * under the stage, a positioned box with its own ground that fills the cell. jsdom paints nothing,
 * so what is pinned is where the mark is: inside the stage, as a layer of its own, beside the
 * aiming mark.
 */
describe("the chosen cell's edge", () => {
	it('is a layer inside the stage, lit on the chosen cell only', () => {
		const { wall } = show('video');
		wall.focused = 0;
		flushSync();

		const cell = host.querySelector('section.cell');
		const edge = host.querySelector('.picked');
		expect(edge, 'the cell drew no edge').not.toBeNull();
		expect(
			edge?.closest('.stage'),
			'the edge is outside the stage, under the picture'
		).not.toBeNull();
		expect(cell?.classList.contains('chosen')).toBe(true);

		wall.focused = 1;
		flushSync();
		expect(cell?.classList.contains('chosen'), 'a cell that is not chosen is lit').toBe(false);
	});

	it('is lit by the chosen class, and goes with the chrome', () => {
		/* The rule, read off the source: jsdom paints nothing, and the edge is drawn at nought
		   opacity on every cell and lifted only on the chosen one. */
		const here = dirname(fileURLToPath(import.meta.url));
		const source = readFileSync(join(here, 'CellMarks.svelte'), 'utf8').replace(/\t/g, '');
		expect(source).toContain('.picked.chosen {\nopacity: 1;\n}');
		expect(source).toContain('.picked.chosen.quiet {\nopacity: 0;\n}');
	});

	it("is the drop target's dashed line, the one the aiming mark draws, not a solid ring", () => {
		/*
		 * The same outline as a download dragged onto a person tile: one rule draws the edge for
		 * both marks, so they cannot drift apart.
		 */
		const here = dirname(fileURLToPath(import.meta.url));
		const source = readFileSync(join(here, 'CellMarks.svelte'), 'utf8').replace(/\t/g, '');
		expect(source).toContain(
			'.picked,\n.aimed {\nposition: absolute;\ninset: 0;\nborder: 2px dashed var(--sift-accent);'
		);
		expect(source, 'the chosen edge went back to a solid ring').not.toMatch(
			/\.picked[^{]*\{[^}]*solid/
		);
	});
});

/*
 * A picture that stops arriving. A stalled stream fires `waiting` or `stalled` and then nothing (no
 * `ended`, no `error`), so without a listener a cell would sit on its last frame. jsdom cannot
 * stall a stream, so the events are sent by hand and the clock is the test's.
 */
describe('a picture that stops arriving', () => {
	/** A cell playing, with its element saying it is not paused: jsdom's always says it is. */
	function playingCell() {
		const shown = show('video');
		shown.wall.paused = false;
		flushSync();
		const video = host.querySelector('video') as HTMLVideoElement;
		Object.defineProperty(video, 'paused', { configurable: true, get: () => false });
		attached.mockClear();
		detach.mockClear();
		return { ...shown, video };
	}

	it('asks for the file again once, then counts it as failed', () => {
		const { cell, video } = playingCell();
		const failed = vi.spyOn(cell, 'failed').mockResolvedValue();

		video.dispatchEvent(new Event('waiting'));
		vi.advanceTimersByTime(STALL_MS - 1);
		expect(attached, 'the cell acted before the limit').not.toHaveBeenCalled();
		vi.advanceTimersByTime(1);
		expect(detach, 'the stalled request was not let go').toHaveBeenCalledTimes(1);
		expect(attached, 'the file was not asked for again').toHaveBeenCalledTimes(1);
		expect(failed).not.toHaveBeenCalled();

		/* No second event: a fresh request that never gets a byte may say nothing at all, so the
		   second chance ends on its own clock. */
		vi.advanceTimersByTime(STALL_MS);
		expect(failed, 'a file that stalled twice left the cell frozen').toHaveBeenCalledWith(
			STALLED_WORDS
		);
	});

	it('is not a stall when the picture moves', () => {
		const { cell, video } = playingCell();
		const failed = vi.spyOn(cell, 'failed').mockResolvedValue();

		video.dispatchEvent(new Event('waiting'));
		vi.advanceTimersByTime(STALL_MS / 2);
		Object.defineProperty(video, 'currentTime', { configurable: true, get: () => 5 });
		video.dispatchEvent(new Event('timeupdate'));
		vi.advanceTimersByTime(STALL_MS * 2);
		expect(attached, 'a file that recovered on its own was asked for again').not.toHaveBeenCalled();
		expect(failed).not.toHaveBeenCalled();
	});

	it('is not a stall while the wall is held', () => {
		const { wall, cell, video } = playingCell();
		const failed = vi.spyOn(cell, 'failed').mockResolvedValue();

		video.dispatchEvent(new Event('waiting'));
		wall.paused = true;
		flushSync();
		vi.advanceTimersByTime(STALL_MS * 2);
		expect(attached, 'a held cell was treated as stalled').not.toHaveBeenCalled();
		expect(failed).not.toHaveBeenCalled();
	});
});

describe("a cell's sound mark", () => {
	/* Read from the stylesheet: jsdom lays nothing out, and what is under test is the shape and
	   the corner two rules give two marks. */
	const here = dirname(fileURLToPath(import.meta.url));
	const source = readFileSync(join(here, 'CellMarks.svelte'), 'utf8').replace(/\t/g, '');
	const rule = (selector: string): string => {
		const at = source.indexOf(`${selector} {`);
		expect(at, selector).toBeGreaterThan(-1);
		return source.slice(at, source.indexOf('}', at));
	};

	it("is the key echo's square, in the corner the echo takes", () => {
		const mark = rule('.hearing,\n.waits');
		expect(mark, 'the sound mark is round again').toContain('border-radius: var(--radius-md);');
		expect(mark).toContain('inline-size: var(--space-8);');
		expect(mark).not.toContain('--radius-full');
		const echo = rule('.echo-spot');
		const markTop = /inset-block-start: ([^;]+);/.exec(mark)![1];
		expect(echo, 'the echo sits somewhere other than the sound mark').toContain(
			`inset-block-start: ${markTop};`
		);
	});

	it('moves the cell number beside the square while it is up, never under it', () => {
		expect(source, 'the number does not follow the sound mark').toContain('class:beside={marked}');
		const beside = rule('.at.beside');
		const mark = rule('.hearing,\n.waits');
		const markLeft = /inset-inline-start: ([^;]+);/.exec(mark)![1];
		const markSize = /inline-size: ([^;]+);/.exec(mark)![1];
		expect(beside, 'the number sits under the square').toContain(
			`inset-inline-start: calc(${markLeft} + ${markSize} + var(--space-1));`
		);
	});
});
