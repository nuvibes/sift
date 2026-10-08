/* The player's own chrome: the scrub preview, and what happens to a file that has no strip.
 *
 * The strips are generated, stored and served from the first import. These are the tests that say
 * the player asks for one, cuts the sheet up the way the server said to,
 * and leaves the scrubber alone when there is no sheet.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import { abLoop } from '$lib/player/loop.svelte';
import { run } from '$lib/player/run.svelte';
import { handover, mini } from '$lib/player/mini.svelte';
import { arrivals } from '$lib/library/changes.svelte';

const detach = vi.fn();
const attach = vi.fn((..._args: unknown[]) => ({ detach }));
/* THIS PLAN HAS NO `qualities`, AND THAT ABSENCE IS LOAD-BEARING. DO NOT ADD ONE.
 *
 * A plan reaching the page without that field is an ordinary thing (an older server, a cached
 * answer, a fixture written before the quality menu existed). A bar reading `plan?.qualities` with
 * the `?.` guarding only the plan would throw on `.length` of the missing field and take the whole
 * bar down, and the type says `qualities: Quality[]`, so nothing type-checked would ever say so: a
 * test double is never type-checked.
 *
 * So the double stays as an OLD server's answer. Giving it the field would make every test here
 * pass and take the guard away with it.
 */
const planFor = vi.fn(async (_id: string) => ({
	route: 'direct',
	reason: 'plays as it is',
	url: '/api/assets/asset-1/stream',
	scale_height: null,
	projected_realtime: null,
	streamable: true,
	duration_ms: 30_000 as number | null,
	resume_ms: null
}));

vi.mock('$lib/player/playback', async () => {
	const real = await vi.importActual<typeof import('$lib/player/playback')>('$lib/player/playback');
	return {
		...real,
		planFor: (id: string) => planFor(id),
		attach: (...args: unknown[]) => attach(...(args as [])),
		startAt: () => null
	};
});

/** What the server says a heart or a star ended up as. Set by whichever test cares. */
let written = { favorite: true, rating: null as number | null };

vi.mock('$lib/api/client', () => ({
	api: {
		post: vi.fn(async () => ({})),
		get: vi.fn(async () => ({})),
		put: vi.fn(async () => written)
	}
}));

vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettingValues: vi.fn(async () => new Map<string, unknown>()),
	saveSettings: vi.fn(async () => {}),
	// The player draws a rating, and the rating scale is a preference that takes effect behind the
	// settings sheet, so it registers a watcher at module scope. A mock without this fails the
	// whole suite at import, which is the honest signal that the module's surface has widened.
	onSettingsSaved: vi.fn()
}));

import PlayerHarness from './PlayerHarness.svelte';
import TileControls from '$lib/components/TileControls.svelte';
import type { SpriteSheet } from '$lib/player/trickplay';
import type { FileFacts } from '$lib/player/facts';
import { fileFacts } from '$lib/design/testing.svelte';

/** Six across, three down, fifteen frames of a thirty-second clip. */
function layout(overrides: Partial<SpriteSheet> = {}): SpriteSheet {
	return { columns: 6, rows: 3, tile_width: 160, frames: 15, ...overrides };
}

let host: HTMLElement;
/* TAKEN DOWN PROPERLY, not merely removed from the page.
 *
 * `host.remove()` takes the markup away and leaves the component running, and this one listens on
 * the WINDOW for every key it answers. So each test would leave another player behind, and every
 * one of them would answer the NEXT test's key press against its own detached video: two presses
 * of L mark a loop at the right two seconds and then a leaked player from an earlier test marks the
 * same loop at ITS position, so the marks read as somebody else's. The describe block for `a file
 * Sift has not read` keeps its own handle for the same reason. */
let mounted: ReturnType<typeof mount> | null = null;

beforeEach(() => {
	vi.clearAllMocks();
	// The bar and the drawer both keep a second of grace before they go, so the clock is driven
	// rather than waited on.
	vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
	vi.useRealTimers();
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
	/* The loop is one object shared by every player, which is what makes the handover work, and
	 * it means a loop left set by one test is still set when the next one opens the same clip. That
	 * is the application's own behaviour, pinned below; here it just has to not leak. */
	abLoop.clear();
});

async function render(
	sprite: SpriteSheet | null = null,
	art: string | null = null,
	id = 'asset-1'
) {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(PlayerHarness, { target: host, props: { id, sprite, art } });
	// The plan is a promise, so the bar does not exist until it has settled. Two turns: one for the
	// plan, one for the preferences read that follows it.
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
	return host;
}

/** The same player, handed the file's own facts: what the panel of stats reads. */
/** The same player, with the picture armed to be dragged out of the window. */
async function renderDraggable() {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(PlayerHarness, { target: host, props: { id: 'asset-1', draggable: true } });
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
	return host;
}

/** The same player, told it is in the small panel in the corner. */
async function renderCompact() {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(PlayerHarness, { target: host, props: { id: 'asset-1', compact: true } });
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
	return host;
}

async function renderWith(known: Partial<FileFacts>) {
	const file = fileFacts(known);
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(PlayerHarness, { target: host, props: { id: 'asset-1', file } });
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
	return host;
}

/** The video says how long it is, which is the first moment a timeline means anything. */
function announceLength(seconds: number) {
	const video = host.querySelector('video') as HTMLVideoElement;
	Object.defineProperty(video, 'duration', { configurable: true, value: seconds });
	video.dispatchEvent(new Event('loadedmetadata'));
	flushSync();
}

/* Open the grid of controls that are about the clip.
 *
 * They sit behind one button rather than along the bar, so anything reaching for one has to
 * open it first, the same as somebody using the app. It opens on a press as well as on hover,
 * which is what makes it reachable without a pointer at all. */
function openTray(): void {
	const glyph = host.querySelector('[aria-label="More controls"]') as HTMLElement;
	(glyph.closest('button') as HTMLElement).click();
	flushSync();
}

/** The one button that sets, then closes, then clears the A-B loop. */
function loopButton(): HTMLElement {
	if (!host.querySelector('[aria-label^="Set the loop"], [aria-label="Clear the loop"]')) {
		openTray();
	}
	const glyph = host.querySelector('[aria-label^="Set the loop"], [aria-label="Clear the loop"]');
	return (glyph as HTMLElement).closest('button') as HTMLElement;
}

/* The timeline itself, which is the shared slider rather than a range input this file dresses.
   Found by what it IS rather than by a class, so the primitive can rename its own internals. */
function scrubber(): HTMLInputElement {
	return host.querySelector('.timeline input[type="range"]') as HTMLInputElement;
}

/**
 * Point at the timeline, `across` pixels along a track 300 wide. jsdom measures nothing itself.
 *
 * The box that is measured is the one the preview is POSITIONED against (the timeline, not the
 * range input inside it) so that a frame cannot be drawn a few pixels away from the moment it is
 * a picture of. The event is still dispatched on the input, because that is what a pointer lands on.
 */
function pointAt(across: number) {
	const box = host.querySelector('.timeline') as HTMLElement;
	box.getBoundingClientRect = () =>
		({ left: 0, width: 300, top: 0, height: 4, right: 300, bottom: 4 }) as DOMRect;
	scrubber().dispatchEvent(
		new PointerEvent('pointermove', { bubbles: true, clientX: across, clientY: 0 })
	);
	flushSync();
}

/** The sheet arriving. jsdom fetches nothing, so its size is stood in for. */
function sheetLoads(width = 960, height = 270) {
	const image = host.querySelector('.strip') as HTMLImageElement;
	Object.defineProperty(image, 'naturalWidth', { configurable: true, value: width });
	Object.defineProperty(image, 'naturalHeight', { configurable: true, value: height });
	image.dispatchEvent(new Event('load'));
	flushSync();
}

describe('the scrub preview', () => {
	it('shows the frame at the moment being pointed at', async () => {
		await render(layout());
		announceLength(30);

		pointAt(150); // halfway along a thirty-second clip: the eighth of fifteen frames
		sheetLoads();

		const frame = host.querySelector('.frame') as HTMLElement;
		expect(frame).not.toBeNull();
		// Frame 7, on a sheet six across: second cell of the second row.
		expect(frame.style.backgroundPosition).toBe('-160px -90px');
		expect(frame.textContent).toContain('0:15');
	});

	it('cuts the sheet up the way the server said to', async () => {
		/*
		 * The layout is read rather than assumed. Told the same sheet is four across instead of six,
		 * the same moment lands on a different cell, which is a frame from somewhere else in the
		 * clip, and looks exactly like a frame from this one.
		 */
		await render(layout({ columns: 4, rows: 4 }));
		announceLength(30);

		pointAt(150);
		sheetLoads();

		const frame = host.querySelector('.frame') as HTMLElement;
		expect(frame.style.backgroundPosition).not.toBe('-160px -90px');
	});

	it('asks for the strip at an address a browser may keep', async () => {
		await render(layout(), 'token-9');
		announceLength(30);

		pointAt(30);

		const image = host.querySelector('.strip') as HTMLImageElement;
		expect(image.getAttribute('src')).toBe('/api/assets/asset-1/sprite?v=token-9');
	});

	it('goes away when the pointer leaves the timeline', async () => {
		await render(layout());
		announceLength(30);
		pointAt(150);
		sheetLoads();

		scrubber().dispatchEvent(new PointerEvent('pointerleave', { bubbles: true }));
		flushSync();

		expect(host.querySelector('.frame')).toBeNull();
	});

	it('draws nothing before the clip has said how long it is', async () => {
		// Every moment would be the first frame, which is a preview that lies rather than one that
		// is missing.
		await render(layout());

		pointAt(150);

		expect(host.querySelector('.strip')).toBeNull();
	});
});

describe('a file Sift has not read', () => {
	/* Mounted and UNMOUNTED here, unlike the rest of the file. `host.remove()` takes the markup and
	   leaves the component alive, with its watchers, and a leaked player still waiting on an
	   unread file would answer the same change these tests ring, doubling every count below. */
	let player: ReturnType<typeof mount> | null = null;

	async function renderWatching() {
		host = document.createElement('div');
		document.body.append(host);
		player = mount(PlayerHarness, {
			target: host,
			props: { id: 'asset-1', sprite: null, art: null }
		});
		await Promise.resolve();
		await Promise.resolve();
		flushSync();
	}

	afterEach(() => {
		if (player) unmount(player);
		player = null;
	});

	const unread = {
		route: 'unread',
		reason: 'Sift has not read this file yet. It will play once it has been read.',
		url: '/api/assets/asset-1/stream',
		scale_height: null,
		projected_realtime: null,
		streamable: true,
		duration_ms: null,
		resume_ms: null
	};

	it('says so, attaches nothing, and offers nothing to overrule', async () => {
		planFor.mockResolvedValueOnce(unread);
		await renderWatching();

		expect(host.textContent).toContain('has not read this file yet');
		expect(host.querySelector('video')).toBeNull();
		expect(attach).not.toHaveBeenCalled();
		expect(host.textContent).not.toContain('Try anyway');
	});

	it('asks again when files move, and plays once the read has landed', async () => {
		planFor.mockResolvedValueOnce(unread);
		await renderWatching();
		expect(planFor).toHaveBeenCalledTimes(1);

		arrivals.changed();
		await Promise.resolve();
		await Promise.resolve();
		flushSync();

		expect(planFor).toHaveBeenCalledTimes(2);
		expect(host.querySelector('video')).not.toBeNull();
		expect(attach).toHaveBeenCalledTimes(1);
	});

	it('does not re-plan a file that is playing when files move', async () => {
		await renderWatching();
		expect(planFor).toHaveBeenCalledTimes(1);

		arrivals.changed();
		await Promise.resolve();
		flushSync();

		expect(planFor).toHaveBeenCalledTimes(1);
	});
});

describe('a file with no strip', () => {
	it('leaves the scrubber exactly as it was', async () => {
		// A still, a clip too short to have one, and a file whose strip has not been built yet are
		// all ordinary. Nothing is drawn and nothing is requested.
		await render(null);
		announceLength(30);

		pointAt(150);

		expect(host.querySelector('.frame')).toBeNull();
		expect(host.querySelector('.strip')).toBeNull();
		expect(scrubber()).not.toBeNull();
	});

	it('and the scrubber still seeks', async () => {
		await render(null);
		announceLength(30);
		const video = host.querySelector('video') as HTMLVideoElement;

		const track = scrubber();
		track.value = '12';
		track.dispatchEvent(new Event('input', { bubbles: true }));

		expect(video.currentTime).toBe(12);
	});

	it('refuses a layout that cannot describe a sheet', async () => {
		// A row from an older build, or one whose numbers cannot be true. Treated as no strip at
		// all rather than cut up into the wrong frames.
		await render(layout({ frames: 99 }));
		announceLength(30);

		pointAt(150);

		expect(host.querySelector('.strip')).toBeNull();
	});
});

describe('the keys', () => {
	/** Press a key on the page, the way somebody watching would. */
	function press(key: string, modifiers: Partial<KeyboardEventInit> = {}) {
		window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, ...modifiers }));
		flushSync();
	}

	async function playing() {
		await render();
		announceLength(60);
		const video = host.querySelector('video') as HTMLVideoElement;
		video.currentTime = 20;
		return video;
	}

	it('move five seconds with the arrows', async () => {
		// The one navigation action a video player is judged on, on the keys every other player
		// puts it on.
		const video = await playing();

		press('ArrowLeft');
		expect(video.currentTime).toBe(15);

		press('ArrowRight');
		expect(video.currentTime).toBe(20);
	});

	it('leave the arrows alone when Shift is held', async () => {
		// Shift and an arrow steps to the next file, which belongs to the screen around this one.
		const video = await playing();

		press('ArrowLeft', { shiftKey: true });

		expect(video.currentTime).toBe(20);
	});

	it('never run off either end', async () => {
		const video = await playing();
		video.currentTime = 2;

		press('ArrowLeft');
		expect(video.currentTime).toBe(0);

		video.currentTime = 58;
		press('ArrowRight');
		expect(video.currentTime).toBe(60);
	});

	/** Let a key go, which is where a held M is finally settled. */
	function release(key: string, modifiers: Partial<KeyboardEventInit> = {}) {
		window.dispatchEvent(new KeyboardEvent('keyup', { key, bubbles: true, ...modifiers }));
		flushSync();
	}

	it('mute and unmute on M, on the key coming UP', async () => {
		/*
		 * The mute happens on the release, because M held down is the modifier for the four volume
		 * keys (the wall's arrangement, key for key). A press-and-let-go behaves the same.
		 */
		const video = await playing();

		press('m');
		release('m');
		expect(video.muted).toBe(true);

		press('M');
		release('M');
		expect(video.muted).toBe(false);
	});

	it('does not mute when M was held as the modifier', async () => {
		// The whole point of settling it on the release: a press that MEANT "louder" must not also
		// flip the sound off on the way back up.
		const video = await playing();

		press('m');
		press('ArrowUp');
		release('m');

		expect(video.muted).toBe(false);
	});

	it('changes the volume with the arrows while M is held, ten and two', async () => {
		const video = await playing();
		// The element carries whole percent as a 0-1 float, and the preference starts at full.
		press('m');

		press('ArrowDown');
		expect(Math.round(video.volume * 100)).toBe(90);

		press('ArrowLeft');
		expect(Math.round(video.volume * 100)).toBe(88);

		press('ArrowRight');
		expect(Math.round(video.volume * 100)).toBe(90);

		press('ArrowUp');
		expect(Math.round(video.volume * 100)).toBe(100);
		release('m');
	});

	it('says where a volume key left the sound, in the corner of the picture', async () => {
		/* The level moves a slider nobody can see while M is held, so the badge a Theater cell
		   raises for the same key is raised here too, in the same words. */
		await playing();
		press('m');
		press('ArrowDown');
		const badge = host.querySelector('.key-echoes [role="status"][aria-label^="Volume"]');
		expect(badge?.getAttribute('aria-label')).toBe('Volume -10 (90)');
		release('m');
		// And the mute key, on its way up, says the state it left.
		press('m');
		release('m');
		expect(host.querySelector('.key-echoes [aria-label="Muted"]')).not.toBeNull();
	});

	it('never takes the volume past either end', async () => {
		const video = await playing();
		press('m');

		press('ArrowUp');
		expect(Math.round(video.volume * 100)).toBe(100);

		for (let n = 0; n < 12; n += 1) press('ArrowDown');
		expect(Math.round(video.volume * 100)).toBe(0);
		release('m');
	});

	it('leaves the arrows seeking once M has been let go', async () => {
		// A flag stuck true is what turns the two arrows into volume keys for the rest of a sitting,
		// which is the fault the wall's own release handler was written for.
		const video = await playing();

		press('m');
		release('m');
		press('ArrowLeft');

		expect(video.currentTime).toBe(15);
	});

	it('steps to the file either side on Ctrl and an arrow', async () => {
		const back = vi.fn();
		const on = vi.fn();
		host = document.createElement('div');
		document.body.append(host);
		mounted = mount(PlayerHarness, {
			target: host,
			props: { id: 'asset-1', onprevious: back, onnext: on }
		});
		await Promise.resolve();
		await Promise.resolve();
		flushSync();
		announceLength(60);
		const video = host.querySelector('video') as HTMLVideoElement;
		video.currentTime = 20;

		press('ArrowRight', { ctrlKey: true });
		expect(on).toHaveBeenCalledTimes(1);

		press('ArrowLeft', { ctrlKey: true });
		expect(back).toHaveBeenCalledTimes(1);
		// And the clip itself stayed where it was: a whole file and five seconds are not a
		// difference somebody can undo by pressing again, so the two must never both answer.
		expect(video.currentTime).toBe(20);
	});

	it('fills the screen on F, and gives it back', async () => {
		const video = await playing();
		const request = vi.fn();
		const exit = vi.fn();
		const stage = video.closest('.stage') as HTMLElement;
		stage.requestFullscreen = request;
		document.exitFullscreen = exit;

		press('f');
		expect(request).toHaveBeenCalled();

		Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: stage });
		press('F');
		expect(exit).toHaveBeenCalled();
		Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: null });
	});

	it('cycles the repeat mode on R, and marks a loop on L', async () => {
		// They are the wall's two, by the same letters, and the drawer's controls are the same two
		// verbs by hand.
		const video = await playing();

		// It starts on playing through, so one press is "repeat this one file".
		press('r');
		openTray();
		expect(host.querySelector('[aria-label="Repeat this"]')).not.toBeNull();

		/* From nothing: the marks are one object shared by every player, so whatever is sitting in it
		   when this starts decides whether the first press is a start or a clear. */
		abLoop.clear();
		video.currentTime = 10;
		press('l');
		video.currentTime = 20;
		press('l');
		expect(abLoop.a).toBe(10);
		expect(abLoop.b).toBe(20);
	});

	it('are ignored while somebody is typing', async () => {
		/* A search box is one Escape away from every screen in the app, and a player that seeks
		 * while somebody types an L into it is a player that cannot be used with a keyboard. */
		const video = await playing();
		const field = document.createElement('input');
		document.body.append(field);

		field.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
		flushSync();

		expect(video.currentTime).toBe(20);
		field.remove();
	});
});

describe('the controls that are about the file', () => {
	/* The heart is not on the player's bar; the judgement state and its test live with the tile. */

	it('sets a loop between two points, and drops it when a different file is opened', async () => {
		/*
		 * The loop belongs to the clip it was marked on, not to the component: handing a clip to
		 * the corner panel builds a second player on the same file, and the loop must survive that.
		 *
		 * Still session-only: it is recorded nowhere, so a reload clears it, and the two markers
		 * are on the timeline the whole time it is set, so a video that will not play past a point
		 * always shows why.
		 */
		await render();
		announceLength(60);
		const video = host.querySelector('video') as HTMLVideoElement;

		video.currentTime = 10;
		loopButton().click();
		flushSync();
		video.currentTime = 20;
		loopButton().click();
		flushSync();

		expect(host.querySelectorAll('.marker')).toHaveLength(2);

		// Something else entirely. The marks were about the last clip and mean nothing here.
		host.remove();
		await render(null, null, 'asset-2');
		announceLength(60);

		expect(
			host.querySelectorAll('.marker'),
			'the marks from the last clip were carried onto this one'
		).toHaveLength(0);
	});

	it('keeps the loop when the same clip is handed to a second player', async () => {
		/*
		 * The handover, which is the case the shared state exists for: the panel in the corner and
		 * the full-size view are two instances of this component, and sending a clip from one to the
		 * other must not throw away what was set on it.
		 */
		await render();
		announceLength(60);
		const video = host.querySelector('video') as HTMLVideoElement;

		video.currentTime = 10;
		loopButton().click();
		flushSync();
		video.currentTime = 20;
		loopButton().click();
		flushSync();
		expect(host.querySelectorAll('.marker')).toHaveLength(2);

		// The same clip, in a second player: what the corner does.
		host.remove();
		await render();
		announceLength(60);

		expect(
			host.querySelectorAll('.marker'),
			'the loop ended when the clip was handed over'
		).toHaveLength(2);
	});

	it('sends the playhead back to the start of the loop when it reaches the end', async () => {
		await render();
		announceLength(60);
		const video = host.querySelector('video') as HTMLVideoElement;

		video.currentTime = 10;
		loopButton().click();
		flushSync();
		video.currentTime = 20;
		loopButton().click();
		flushSync();

		// Playing on past the second point, the way it would a moment later.
		video.currentTime = 21;
		video.dispatchEvent(new Event('timeupdate'));
		flushSync();

		expect(video.currentTime).toBe(10);
	});

	it('will not let the loop run backwards', async () => {
		// A second press before the first point is somebody correcting themselves.
		await render();
		announceLength(60);
		const video = host.querySelector('video') as HTMLVideoElement;

		video.currentTime = 30;
		loopButton().click();
		flushSync();
		video.currentTime = 5;
		loopButton().click();
		flushSync();

		expect(host.querySelectorAll('.marker'), 'a loop that ends before it starts').toHaveLength(1);
	});
});

describe('the grid of controls behind one button', () => {
	/* Sixteen controls on one row would squeeze the timeline to a stub, so the ones that are about
	 * the clip rather than about the playhead sit behind a single button. These say none of them is
	 * quietly lost, and that the ones left on the row are the ones meant to be there. */

	it('draws nothing until it is opened', async () => {
		await render();

		expect(host.querySelector('.panel'), 'the grid is open before anybody asked').toBeNull();
		expect(host.querySelector('[aria-label="Stats for nerds"]')).toBeNull();
	});

	it('opens under the pointer and closes when it leaves', async () => {
		// Hover is what makes it worth having: it costs nothing to look in. A press works too, which
		// is what makes it reachable with no pointer at all: that is what `openTray` uses.
		await render();
		const tray = host.querySelector('.tray') as HTMLElement;

		tray.dispatchEvent(new PointerEvent('pointerenter', { bubbles: false }));
		flushSync();
		expect(host.querySelector('.panel'), 'hovering did not open it').not.toBeNull();

		/* The grid floats clear of its button, and the pointer has to cross that gap to reach it.
		 * The gap is inside a transparent box that reaches down to the button, so the pointer's path never
		 * leaves the drawer: without it the drawer would close on the way to itself and nothing in
		 * it could be pressed. The box is what this asserts; the closing below is instant, as it should
		 * be once the gap is not a hole. */
		expect(
			host.querySelector('.bridge'),
			'nothing bridges the gap between the button and the grid'
		).not.toBeNull();

		tray.dispatchEvent(new PointerEvent('pointerleave', { bubbles: false }));
		flushSync();
		expect(host.querySelector('.panel'), 'it stayed open after the pointer left').toBeNull();
	});

	it('stays open when pressed after the pointer opened it, and a second press shuts it', async () => {
		// Pointing at the button opens the drawer on the way to pressing it, and a touch is both.
		await render();
		const tray = host.querySelector('.tray') as HTMLElement;
		const button = host.querySelector('[aria-label="More controls"]') as HTMLElement;

		tray.dispatchEvent(new PointerEvent('pointerenter', { bubbles: false }));
		flushSync();
		button.click();
		flushSync();
		expect(
			host.querySelector('.panel'),
			'the press shut a drawer the pointer opened'
		).not.toBeNull();

		button.click();
		flushSync();
		expect(host.querySelector('.panel'), 'a second press left it open').toBeNull();

		button.click();
		flushSync();
		expect(host.querySelector('.panel'), 'a press on a shut drawer did not open it').not.toBeNull();
	});

	it('holds every control that came off the row', async () => {
		/*
		 * The check against a control being lost rather than relocated. Each is asked for
		 * by the name it announces itself with, so a button that is drawn but unnamed fails here:
		 * an icon with no label is a control some people cannot use at all.
		 */
		await render();
		openTray();
		const panel = host.querySelector('.panel') as HTMLElement;

		expect(panel.querySelector('[aria-label="Set the loop start"]')).not.toBeNull();
		expect(panel.querySelector('[aria-label="Stats for nerds"]')).not.toBeNull();
		expect(panel.querySelector('[aria-label="Play something else"]')).toBeNull();
	});

	it('keeps Repeat and Shuffle in the drawer beside Randomize, off the row', async () => {
		/* The row is the step pair and Play; the two about what comes next stand with Randomize, the
		   third, as on Theater's bar. */
		await render();
		const transport = [...host.querySelectorAll('.player-bar .middle button')].map((one) =>
			one.getAttribute('aria-label')
		);
		expect(transport).toContain('Play');
		expect(transport, 'Repeat is still on the row').not.toContain('Play through');
		expect(transport, 'Shuffle is still on the row').not.toContain('Shuffle');

		openTray();
		const panel = host.querySelector('.panel') as HTMLElement;
		const drawer = [...panel.querySelectorAll('button')].map((one) =>
			one.getAttribute('aria-label')
		);
		const randomize = drawer.indexOf('Nothing here can open a random file');
		expect(drawer.slice(randomize + 1, randomize + 3)).toEqual(['Play through', 'Shuffle']);
	});

	it('leaves the mini player on the row, and the judgement controls off it', async () => {
		/* What stays on the row is what is reached for mid-clip. The heart and the stars are on the
		 * tile and on the detail sheet, where there is room to hit them; the mini player is not
		 * anywhere else, so burying it behind another press is a control that stops being used. */
		await render();

		expect(host.querySelector('.bar [aria-label="Open mini player"]')).not.toBeNull();
		expect(host.querySelector('.bar .heart'), 'the heart is still on the row').toBeNull();
		expect(host.querySelector('.bar .stars'), 'the stars are still on the row').toBeNull();
	});

	it('closes on Escape once, and leaves Escape alone the rest of the time', async () => {
		// Escape closes whatever the player is drawn inside. Swallowing it whenever the grid happens
		// to be shut would trap somebody in a dialog they opened a video in.
		await render();
		openTray();

		const swallowed = () => {
			const event = new KeyboardEvent('keydown', {
				key: 'Escape',
				bubbles: true,
				cancelable: true
			});
			window.dispatchEvent(event);
			flushSync();
			return event.defaultPrevented;
		};

		expect(swallowed(), 'Escape did not close the grid').toBe(true);
		expect(host.querySelector('.panel')).toBeNull();
		expect(swallowed(), 'Escape was swallowed with nothing open').toBe(false);
	});
});

describe('the drawer keeps one shape', () => {
	/* Every control is drawn, dimmed with its reason where it cannot act, and the three that open a
	   menu stand in the top row, so each opens above the drawer rather than over its own first row.
	   Nine controls, three rows of three. */
	it('draws nine controls, the menus first, the ones that cannot act dimmed', async () => {
		await render();
		openTray();
		const buttons = [...(host.querySelector('.panel') as HTMLElement).querySelectorAll('button')];
		const named = (one: HTMLElement) =>
			one.getAttribute('aria-label') ??
			one.querySelector('[aria-label]')?.getAttribute('aria-label') ??
			'';

		expect(buttons).toHaveLength(9);
		expect(buttons.slice(0, 3).map(named)).toEqual([
			'Clip',
			expect.stringMatching(/frame|picture|screenshot/i),
			'This file has one size'
		]);
		const dimmed = buttons.filter((one) => (one as HTMLButtonElement).disabled).map(named);
		expect(dimmed).toEqual([
			'This file has one size',
			'Nothing here can open a random file',
			'Mark both ends of a loop to save it'
		]);
	});
});

describe('keeping the last few seconds', () => {
	/* The menu is portalled, and while it is open the library takes the pointer off the page, which
	 * the drawer reads as the pointer having left. Closing the drawer would take the menu with it,
	 * and the button would do nothing at all. */
	it('holds the drawer open while its menu is open', async () => {
		await render();
		openTray();
		const keep = (host.querySelector('[aria-label="Clip"]') as HTMLElement).closest(
			'button'
		) as HTMLElement;
		keep.focus();
		keep.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		flushSync();
		await tick();
		flushSync();
		expect(document.querySelector('[role="menu"]'), 'the menu never opened').not.toBeNull();

		(host.querySelector('.tray') as HTMLElement).dispatchEvent(
			new PointerEvent('pointerleave', { bubbles: false })
		);
		flushSync();

		expect(
			host.querySelector('[aria-label="Clip"]'),
			'the drawer shut under its menu'
		).not.toBeNull();
		expect(document.querySelector('[role="menu"]'), 'the menu went with the drawer').not.toBeNull();
	});
});

describe('the sizes it can be watched at', () => {
	/* The plan double in this file deliberately carries NO `qualities` (see the note at the top,
	   which is load-bearing) so the one test that needs sizes supplies its own answer for one
	   call rather than changing the shared one. */
	function withSizes() {
		planFor.mockResolvedValueOnce({
			route: 'direct',
			reason: 'plays as it is',
			url: '/api/assets/asset-1/stream',
			scale_height: null,
			projected_realtime: null,
			streamable: true,
			duration_ms: 30_000,
			resume_ms: null,
			qualities: [
				{ label: 'Original', detail: '2.1 GB', url: '/api/assets/asset-1/stream', smooth: true },
				{ label: '720p', detail: null, url: '/api/assets/asset-1/stream?h=720', smooth: true },
				/*
				 * One rung the server does not expect this device to keep up with: flagged, but
				 * its name is still its size.
				 */
				{ label: '480p', detail: null, url: '/api/assets/asset-1/stream?h=480', smooth: false }
			]
		} as unknown as Awaited<ReturnType<typeof planFor>>);
	}

	/** The panel, wherever in the document it has been portalled to. */
	function panel(): HTMLElement | null {
		return document.querySelector('.popover-panel');
	}

	/** The control the panel opens from. `aria-pressed` on it IS whether the menu is open. */
	function trigger(): HTMLElement | null {
		return (host.querySelector('[aria-label="Quality"]') as HTMLElement)?.closest('button') ?? null;
	}

	afterEach(() => {
		for (const stale of document.querySelectorAll('.popover-panel')) stale.remove();
	});

	it('opens beside the button rather than as a second row under the bar', async () => {
		/* Not a panel handed to the bar's `below`, drawn across the full width of the bar and several
		 * controls away from what was pressed: a popover opens on the control it belongs to, and the
		 * catch (a layer at the end of the document is not DRAWN while a player is fullscreen) is
		 * answered by `portalTo`, which every other menu in
		 * Sift already uses for exactly this. */
		withSizes();
		await render();
		openTray();

		expect(panel(), 'open before anything was pressed').toBeNull();
		(host.querySelector('[aria-label="Quality"]') as HTMLElement).closest('button')!.click();
		flushSync();

		const open = panel();
		expect(open, 'the sizes never opened').not.toBeNull();
		expect(open?.getAttribute('aria-label')).toBe('Quality');
		expect(host.querySelector('.player-bar .qualities'), 'still under the bar').toBeNull();
	});

	it('holds the drawer open while the sizes are being read', async () => {
		/* The panel is portalled OUT of the drawer, so the pointer moving onto it reads inside the
		 * drawer as the pointer having left, and the drawer closes the moment it does. It would
		 * take the sizes with it, because the button they hang off lives in there. */
		withSizes();
		await render();
		openTray();
		(host.querySelector('[aria-label="Quality"]') as HTMLElement).closest('button')!.click();
		flushSync();

		(host.querySelector('.tray') as HTMLElement).dispatchEvent(
			new PointerEvent('pointerleave', { bubbles: false })
		);
		flushSync();

		expect(panel(), 'the sizes went with the drawer').not.toBeNull();
		expect(host.querySelector('[aria-label="Quality"]'), 'the drawer shut under it').not.toBeNull();
	});

	it('keeps the bar up while the pointer is inside the sizes, which are outside the picture', async () => {
		/*
		 * The panel is PORTALLED, so walking the pointer into it crosses the stage's edge and fires
		 * the stage's `pointerleave`, and a second later the bar, the drawer and this menu would
		 * all fade out from under the hand reading them. The stage offers "the pointer is on the
		 * controls", and the menu calls it from out here.
		 */
		withSizes();
		await render();
		openTray();
		(host.querySelector('[aria-label="Quality"]') as HTMLElement).closest('button')!.click();
		flushSync();

		const stage = host.querySelector('.stage') as HTMLElement;
		stage.dispatchEvent(new PointerEvent('pointerleave', { bubbles: false }));
		(panel()?.querySelector('.qualities') as HTMLElement).dispatchEvent(
			new PointerEvent('pointerenter', { bubbles: false })
		);
		// Past the grace the stage keeps after the pointer leaves the picture.
		vi.advanceTimersByTime(2000);
		flushSync();

		expect(stage.classList.contains('pointed'), 'the bar went from under the pointer').toBe(true);
		expect(stage.classList.contains('resting'), 'the controls faded while in use').toBe(false);
		expect(panel(), 'the sizes went with the bar').not.toBeNull();
	});

	it('shuts the sizes when the bar finally does go, rather than leaving them over the picture', async () => {
		/*
		 * THE OTHER DIRECTION OF THE TEST ABOVE, and the two are a pair.
		 *
		 * Holding the bar up while the pointer is inside the menu is right. What it cannot do is hold
		 * it up for ever: walk the pointer out of the menu and off the picture, the clock runs out,
		 * and a panel of five sizes would be left hanging over the video anchored to a control that is no
		 * longer drawn. The menu follows the bar: `StageHandle.showing`, which is the bar's own
		 * clock rather than a second one kept in step by hand.
		 */
		withSizes();
		await render();
		openTray();
		(host.querySelector('[aria-label="Quality"]') as HTMLElement).closest('button')!.click();
		flushSync();
		expect(panel(), 'the sizes never opened').not.toBeNull();

		const stage = host.querySelector('.stage') as HTMLElement;
		(panel()?.querySelector('.qualities') as HTMLElement).dispatchEvent(
			new PointerEvent('pointerleave', { bubbles: false })
		);
		stage.dispatchEvent(new PointerEvent('pointerleave', { bubbles: false }));
		vi.advanceTimersByTime(4000);
		flushSync();

		expect(stage.classList.contains('resting'), 'the bar never went').toBe(true);
		/* The TRIGGER rather than the panel: the panel leaves on a transition, which is driven by
		   animation frames rather than by the clock, so under a faked clock it stands there mid-fade
		   for ever. `aria-pressed` is the open state itself. */
		expect(
			trigger()?.getAttribute('aria-pressed'),
			'the sizes were left over the picture with no bar under them'
		).toBe('false');
	});

	it('does not wake the bar back up by closing the sizes with it', async () => {
		/* The close fires the same handler a press on the trigger does, and a plain `releaseBar()`
		   there WAKES the stage, so the bar would come straight back for another two and a half
		   seconds every time it tried to leave with the menu open. */
		withSizes();
		await render();
		openTray();
		(host.querySelector('[aria-label="Quality"]') as HTMLElement).closest('button')!.click();
		flushSync();

		const stage = host.querySelector('.stage') as HTMLElement;
		stage.dispatchEvent(new PointerEvent('pointerleave', { bubbles: false }));
		vi.advanceTimersByTime(1500);
		flushSync();
		expect(trigger()?.getAttribute('aria-pressed'), 'the sizes stayed open').toBe('false');

		// A moment later still, with nothing else touched: the bar must not have come back.
		vi.advanceTimersByTime(500);
		flushSync();
		expect(stage.classList.contains('resting'), 'closing the menu woke the bar again').toBe(true);
	});

	it('marks the size being watched, and moves the mark when another is chosen', async () => {
		withSizes();
		await render();
		openTray();
		(host.querySelector('[aria-label="Quality"]') as HTMLElement).closest('button')!.click();
		flushSync();

		const choices = [...(panel()?.querySelectorAll('button') ?? [])] as HTMLButtonElement[];
		expect(choices.map((one) => one.textContent?.trim())).toEqual([
			'Original (2.1 GB)',
			'720p',
			// A RUNG'S NAME IS ITS SIZE. The projection the server sends with it is still sent and is
			// still worth showing; a prediction welded onto a button's name makes a list of sizes read
			// as a list of warnings, with the height somebody is scanning for buried at the front.
			'480p'
		]);
		// The file's own entry to start with: that is what the plan says is playing.
		expect(choices[0].getAttribute('aria-pressed')).toBe('true');
		expect(choices[1].getAttribute('aria-pressed')).toBe('false');
	});
});

describe('what happens when the clip ends', () => {
	/** The control, whatever answer it is currently showing. */
	const endControl = () =>
		host.querySelector(
			'.panel [aria-label^="Stop at"], .panel [aria-label^="Repeat"], ' +
				'.panel [aria-label^="Play through"]'
		) as HTMLElement | null;

	const press = () => {
		(endControl()?.closest('button') as HTMLElement).click();
		flushSync();
	};

	it('starts on carrying on to the next file', async () => {
		/* The same answer a Theater cell starts on. A player and a wall disagreeing about what the
		 * end of a file means is worse than either answer on its own, and stopping at the end of
		 * every clip in a library of short clips is somebody pressing play every few seconds. */
		await render();
		openTray();

		expect(endControl()?.getAttribute('aria-label')).toBe('Play through');
	});

	/*
	 * THE ORDINARY REPEAT BUTTON: play through, then this one file, then off, then round again.
	 *
	 * Not play through, stop, repeat: narrowest first reads correctly as three separate answers and
	 * wrong as a repeat button. One press from the default would take the commonest setting to the
	 * rarest, and getting to "repeat this" would mean passing through the one that stops.
	 */
	it('is one control with three answers, cycling back round', async () => {
		await render();
		openTray();

		press();
		expect(endControl()?.getAttribute('aria-label')).toBe('Repeat this');
		press();
		expect(endControl()?.getAttribute('aria-label')).toBe('Stop at the end');
		press();
		expect(
			endControl()?.getAttribute('aria-label'),
			'the third press did not come back round to the answer it started on'
		).toBe('Play through');
	});

	/*
	 * AND IT IS LIT ONLY WHILE SOMETHING REPEATS. "Stop at the end" is the repeat arrows unlit,
	 * because that is what it is: nothing repeating. A solid stop glyph of its own, pressed
	 * permanently, would put a control that looks like it will stop something NOW in a row of quiet
	 * settings, and make one question look like three unrelated buttons.
	 */
	it('is lit only while something repeats, and never wears a stop glyph', async () => {
		await render();
		openTray();
		const button = () => endControl()?.closest('button') as HTMLElement;

		expect(button().getAttribute('aria-pressed')).toBe('true');
		expect(button().textContent).not.toContain('stop_circle');

		press();
		expect(button().getAttribute('aria-pressed'), 'repeat this is not lit').toBe('true');

		press();
		expect(
			button().getAttribute('aria-pressed'),
			'stop at the end is drawn as a repeat that is ON'
		).toBe('false');
		expect(button().textContent).not.toContain('stop_circle');
	});
});

describe('A, for audio only', () => {
	afterEach(() => mini.close());

	it("hands the clip to the audio-only bar, the press the bar's Open audio player makes", async () => {
		await render();
		const press = new KeyboardEvent('keydown', { key: 'a', bubbles: true, cancelable: true });
		window.dispatchEvent(press);
		flushSync();

		expect(press.defaultPrevented).toBe(true);
		expect(mini.asset?.id).toBe('asset-1');
		expect(mini.bar).toBe(true);
	});
});

describe('handing over to the corner', () => {
	afterEach(() => mini.close());

	function handOver() {
		const press = host.querySelector('.bar [aria-label="Open mini player"]');
		(press?.closest('button') as HTMLElement).click();
		flushSync();
	}

	it('hands over playing before the first frame, since nobody paused it', async () => {
		await render();

		handOver();

		expect(mini.asset?.id).toBe('asset-1');
		expect(mini.asset?.paused).toBe(false);
	});

	it('hands over held once the person has paused it', async () => {
		await render();
		announceLength(30);
		const video = host.querySelector('video') as HTMLVideoElement;
		Object.defineProperty(video, 'paused', { configurable: true, value: true });

		handOver();

		expect(mini.asset?.paused).toBe(true);
	});
});

describe('repeat this, then Shuffle, then the repeat control', () => {
	/*
	 * The sequence as it is pressed: Repeat this on a file played in order, Shuffle turned on, then
	 * ONE press, which is Stop at the end (the same arrows, unlit, no glyph of its own). Under
	 * Shuffle that stops at the end of the SHUFFLED LIST, not of the file,
	 * so the file's end carries the run on and the walk stops where the list runs out
	 * (`asset-view.test.ts`). One more press is Play through, which carries it on as well. With
	 * Shuffle off again, Stop at the end stops at the file's end.
	 */
	const endControl = () =>
		host.querySelector(
			'.panel [aria-label^="Stop at"], .panel [aria-label^="Repeat"], ' +
				'.panel [aria-label^="Play through"]'
		) as HTMLElement | null;
	const pressRepeat = () => {
		(endControl()?.closest('button') as HTMLElement).click();
		flushSync();
	};

	it('carries the run on at the end of the file once the control reads Play through', async () => {
		const onplayedthrough = vi.fn();
		host = document.createElement('div');
		document.body.append(host);
		mounted = mount(PlayerHarness, { target: host, props: { id: 'asset-1', onplayedthrough } });
		await Promise.resolve();
		await Promise.resolve();
		flushSync();
		openTray();
		const video = host.querySelector('video') as HTMLVideoElement;
		const ended = () => {
			video.dispatchEvent(new Event('ended'));
			flushSync();
		};

		pressRepeat();
		expect(endControl()?.getAttribute('aria-label')).toBe('Repeat this');
		ended();
		expect(onplayedthrough).not.toHaveBeenCalled();

		(host.querySelector('[aria-label="Shuffle"]')?.closest('button') as HTMLElement).click();
		flushSync();
		expect(run.shuffle).toBe(true);

		pressRepeat();
		expect(endControl()?.getAttribute('aria-label')).toBe('Stop at the end');
		expect(endControl()?.closest('button')?.getAttribute('aria-pressed')).toBe('false');
		ended();
		expect(
			onplayedthrough,
			'Stop at the end under Shuffle stopped at the file'
		).toHaveBeenCalledTimes(1);

		pressRepeat();
		expect(endControl()?.getAttribute('aria-label')).toBe('Play through');
		ended();
		expect(onplayedthrough).toHaveBeenCalledTimes(2);

		run.toggle();
		pressRepeat();
		pressRepeat();
		expect(endControl()?.getAttribute('aria-label')).toBe('Stop at the end');
		ended();
		expect(
			onplayedthrough,
			'Stop at the end without Shuffle carried the run on'
		).toHaveBeenCalledTimes(2);
	});
});

describe('the facts panel', () => {
	it('says what the file is encoded with, and how many frames a second', async () => {
		/* The container is the word on the end of the filename and decides nothing: an mp4 holding
		 * AV1 and an mp4 holding H.264 are the same word and a different answer to "can this browser
		 * play it". Both are stored from the first import, and both are sent. */
		await renderWith({ vcodec: 'av1', acodec: 'opus', fps: 29.97 });
		openTray();
		(host.querySelector('[aria-label="Stats for nerds"]') as HTMLElement)
			.closest('button')!
			.click();
		flushSync();

		const facts = host.querySelector('.stats') as HTMLElement;
		expect(facts).not.toBeNull();
		// Written the way the encoders are named, not the way ffmpeg spells them.
		expect(facts.textContent).toContain('AV1 / Opus');
		expect(facts.textContent).toContain('29.97 fps');
	});

	it('names the shape of the picture where the shape has a name', async () => {
		/* Reduced by the greatest common divisor rather than matched against a table, so an ordinary
		 * shape reads as one and an odd crop falls back to a decimal rather than to "683:384". */
		await renderWith({ width: 1920, height: 1080 });
		openTray();
		(host.querySelector('[aria-label="Stats for nerds"]') as HTMLElement)
			.closest('button')!
			.click();
		flushSync();

		expect((host.querySelector('.stats') as HTMLElement).textContent).toContain('16:9');
	});

	it('falls back to a decimal on a shape nobody says out loud', async () => {
		await renderWith({ width: 683, height: 384 });
		openTray();
		(host.querySelector('[aria-label="Stats for nerds"]') as HTMLElement)
			.closest('button')!
			.click();
		flushSync();

		const facts = (host.querySelector('.stats') as HTMLElement).textContent ?? '';
		expect(facts, 'a ratio nobody could read was reported as one').not.toContain('683:384');
		expect(facts).toContain('1.78:1');
	});

	it('says so plainly when a file was never probed', async () => {
		// Absent is ordinary: a file indexed a moment ago has none of this yet. Reporting nothing
		// at all in that case reads as a panel that is broken rather than a file that is new.
		await renderWith({ vcodec: null, acodec: null, fps: null });
		openTray();
		(host.querySelector('[aria-label="Stats for nerds"]') as HTMLElement)
			.closest('button')!
			.click();
		flushSync();

		const facts = host.querySelector('.stats') as HTMLElement;
		expect(facts.textContent).toContain('Unknown');
	});

	it('reports whichever half of the encoding is known', async () => {
		// A file with no sound track has no audio codec, and that is not a file with no codecs.
		await renderWith({ vcodec: 'h264', acodec: null, fps: 60 });
		openTray();
		(host.querySelector('[aria-label="Stats for nerds"]') as HTMLElement)
			.closest('button')!
			.click();
		flushSync();

		const facts = host.querySelector('.stats') as HTMLElement;
		expect(facts.textContent).toContain('H.264');
		expect(facts.textContent, 'an empty half was drawn as a separator').not.toContain('H.264 /');
		expect(facts.textContent).toContain('60 fps');
	});
});

describe('the order the facts are in', () => {
	it('is alphabetical, so nobody has to know how they were grouped', async () => {
		/* A reference panel is read by somebody looking for one line. Grouped by meaning it reads as
		 * arbitrary to everybody except whoever grouped it, and a line added later lands wherever the
		 * person adding it thought it belonged. */
		await renderWith({ vcodec: 'h264', fps: 30, width: 1920, height: 1080 });
		openTray();
		(host.querySelector('[aria-label="Stats for nerds"]') as HTMLElement)
			.closest('button')!
			.click();
		flushSync();

		const names = [...host.querySelectorAll('.stats dt')].map((node) => node.textContent ?? '');

		expect(names.length).toBeGreaterThan(5);
		expect(names).toEqual([...names].sort((a, b) => a.localeCompare(b)));
	});
});

describe('the player in the small panel', () => {
	/*
	 * The bar is not drawn there at all, and that is not a preference.
	 *
	 * A bar is PLACED by the frame it is drawn in, through a class the frame reaches for. The corner
	 * panel is not a stage and has no such rule, so a bar there would be ordinary flow content
	 * underneath a video already filling the box: pushed out of the panel and clipped away by it.
	 * Invisible, and still mounted: a scrubber, a play button and a fullscreen button that nobody
	 * could see and Tab would still walk through. The panel draws its own controls; see `MiniPlayer`.
	 */

	it('draws no bar', async () => {
		await renderCompact();

		expect(host.querySelector('[aria-label="Playback controls"]')).toBeNull();
	});

	it('leaves nothing off-screen for the keyboard to find', async () => {
		await renderCompact();

		// Everything the bar carries. None of it should be reachable, because none of it is drawn.
		expect(host.querySelector('.timeline input[type="range"]')).toBeNull();
		expect(host.querySelector('[aria-label="Open mini player"]')).toBeNull();
		expect(host.querySelector('[aria-label="More controls"]')).toBeNull();
	});

	it('still says how far through it is', async () => {
		// The hairline stays: it is what the panel shows at rest, and the panel is what places it.
		await renderCompact();

		expect(host.querySelector('.player-progress')).not.toBeNull();
	});

	it('draws the bar everywhere else', async () => {
		// The other half of the same claim. Without this the test above passes on a player that has
		// stopped drawing a bar anywhere.
		await render();

		expect(host.querySelector('[aria-label="Playback controls"]')).not.toBeNull();
	});
});

describe('looking closer at a moving picture', () => {
	/*
	 * The same magnifier a photograph has had all along, pointed at the video.
	 *
	 * Fullscreen only, which is the rule the still already follows rather than a limitation: in the
	 * window the wheel scrolls what is behind the player and a drag is how a file leaves the window
	 * for another application, so magnifying here would take both of those away from what they
	 * already mean.
	 */

	function goFullscreen() {
		Object.defineProperty(document, 'fullscreenElement', {
			configurable: true,
			value: host.querySelector('.stage')
		});
		document.dispatchEvent(new Event('fullscreenchange'));
		flushSync();
	}

	function screen() {
		return host.querySelector('video') as HTMLVideoElement;
	}

	function wheel(deltaY: number, at = { clientX: 0, clientY: 0 }) {
		screen().dispatchEvent(
			new WheelEvent('wheel', { deltaY, ...at, bubbles: true, cancelable: true })
		);
		flushSync();
	}

	function drag(to: { clientX: number; clientY: number }) {
		screen().dispatchEvent(
			new PointerEvent('pointerdown', { button: 0, clientX: 0, clientY: 0, bubbles: true })
		);
		screen().dispatchEvent(new PointerEvent('pointermove', { ...to, bubbles: true }));
		screen().dispatchEvent(new PointerEvent('pointerup', { ...to, bubbles: true }));
		flushSync();
	}

	afterEach(() => {
		Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: null });
	});

	it('does nothing in a window, where the wheel already scrolls the page', async () => {
		await render();

		wheel(-300);

		expect(screen().style.scale).toBe('');
	});

	it('magnifies once the clip has the screen to itself', async () => {
		await render();
		goFullscreen();

		wheel(-300);

		expect(Number(screen().style.scale)).toBeGreaterThan(1);
	});

	it('pans on a drag, once magnified', async () => {
		await render();
		goFullscreen();
		wheel(-300);

		drag({ clientX: 40, clientY: 25 });

		const [x, y] = screen().style.translate.split(' ').map(parseFloat);
		expect(x).toBe(40);
		expect(y).toBe(25);
	});

	it('does not pause the clip at the end of a pan', async () => {
		/* A press on the video plays and pauses, and a pan is a press. Letting go after dragging a
		 * magnified picture must not stop the clip, and it is not a case anybody would think to try,
		 * because a photograph has no playback for a stray press to reach. */
		await render();
		goFullscreen();
		wheel(-300);
		const video = screen();
		const pause = vi.spyOn(video, 'pause');
		Object.defineProperty(video, 'paused', { configurable: true, value: false });

		drag({ clientX: 40, clientY: 25 });
		video.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		expect(pause).not.toHaveBeenCalled();
	});

	it('still pauses on a press that did not travel', async () => {
		// The other half. Without this the test above passes on a player that has stopped answering
		// a press at all.
		await render();
		goFullscreen();
		wheel(-300);
		const video = screen();
		const pause = vi.spyOn(video, 'pause');
		Object.defineProperty(video, 'paused', { configurable: true, value: false });

		video.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		expect(pause).toHaveBeenCalled();
	});

	it('stands the drag-out down while it is magnified', async () => {
		/* A native drag and a pan are the same gesture and the operating system's one wins, so
		 * dragging a magnified clip would post the file to another application instead of moving the
		 * picture. */
		await renderDraggable();
		goFullscreen();

		// Armed, until there is something to pan.
		expect(screen().getAttribute('draggable')).toBe('true');

		wheel(-300);

		expect(screen().getAttribute('draggable')).not.toBe('true');
	});

	it('starts again at fitting when the screen is given back', async () => {
		await render();
		goFullscreen();
		wheel(-300);
		expect(screen().style.scale).not.toBe('');

		Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: null });
		document.dispatchEvent(new Event('fullscreenchange'));
		flushSync();

		expect(screen().style.scale).toBe('');
	});
});

/*
 * The wall bar's aim report, on the two surfaces that do not want it.
 *
 * `PlayerBar` has `onaim` for Theater: pointing at a verb washes the cell it is about to act on,
 * because a wall has up to nine and nothing on the bar says which. The full-size player and the
 * mini panel hand it nothing, since there is one thing on screen.
 *
 * `aims` is an empty object when `onaim` is absent, so the controls on those two surfaces carry no
 * listeners at all. The guard protects against the obvious way to write it, four handlers calling
 * `onaim` directly, which would throw on the first hover outside Theater and take the bar down.
 */
describe('the bar on a surface that has nothing to aim at', () => {
	it('is pointed at without a handler to call, and nothing goes wrong', async () => {
		await render(layout());
		announceLength(30);

		const controls = [...host.querySelectorAll('button')];
		expect(controls.length, 'the bar drew no controls to point at').toBeGreaterThan(3);

		expect(() => {
			for (const one of controls) {
				one.dispatchEvent(new MouseEvent('mouseenter', { bubbles: false }));
				one.dispatchEvent(new FocusEvent('focus', { bubbles: false }));
				one.dispatchEvent(new MouseEvent('mouseleave', { bubbles: false }));
				one.dispatchEvent(new FocusEvent('blur', { bubbles: false }));
			}
			flushSync();
		}, 'a control reported an aim on a surface that passed nowhere to report it').not.toThrow();

		// And the bar is still there afterwards, which is what an unguarded call would have cost.
		expect(scrubber(), 'the bar came down').not.toBeNull();
	});
});

describe('a finger on the picture', () => {
	/* A tap is play and pause and nothing else: two quick taps are somebody changing their mind,
	 * not a request to fill the screen. A mouse's double click still fills it. */
	function doublePress(video: HTMLVideoElement, pointerType: string) {
		for (let i = 0; i < 2; i++) {
			video.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerType }));
			video.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		}
		video.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
	}

	it('does not fill the screen on a double tap, and does on a double click', async () => {
		await render();
		const video = host.querySelector('video') as HTMLVideoElement;
		const stage = video.closest('.stage') as HTMLElement;
		const request = vi.fn(async () => {});
		stage.requestFullscreen = request;

		doublePress(video, 'touch');
		expect(request).not.toHaveBeenCalled();

		doublePress(video, 'mouse');
		expect(request).toHaveBeenCalledOnce();
	});
});

describe('arriving from the corner to fill the screen', () => {
	/* F on the corner panel or the audio-only bar: those have no stage to fill the screen with, so
	   the clip comes back to full size and the full-size stage fills it as it arrives. */
	const had = HTMLElement.prototype.requestFullscreen;
	afterEach(() => {
		HTMLElement.prototype.requestFullscreen = had;
		handover.takeFill('asset-1');
	});

	it('fills the screen once, when the panel asked for it', async () => {
		const request = vi.fn(async () => {});
		HTMLElement.prototype.requestFullscreen = request;
		handover.fillsTheScreen('asset-1');

		await render();

		expect(request).toHaveBeenCalledOnce();
		expect(handover.takeFill('asset-1'), 'the ask is spent on arrival').toBe(false);
	});

	it('does not fill the screen on an ordinary arrival', async () => {
		const request = vi.fn(async () => {});
		HTMLElement.prototype.requestFullscreen = request;

		await render();

		expect(request).not.toHaveBeenCalled();
	});
});
