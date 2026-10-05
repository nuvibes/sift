/* The panel in the corner: what it draws for a clip, what it draws for a picture, and what it does
 * not draw for either.
 *
 * Three of the things checked here are easy faults rather than choices. A full player bar that no
 * rule places is pushed out of the box and clipped: invisible, mounted, and in the tab order. A
 * panel that cannot hold a photograph loses a run of mixed media at every picture in it. And a
 * progress hairline of its own would be a second copy of the one the player already draws.
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const goto = vi.fn();
/* `beforeNavigate` as well as `goto`, and it is not decoration: this file's own mock REPLACES the
   default in `test-setup.ts`, so any export the tree reaches for and this does not name is a hard
   error at mount. The panel draws a `Player`, and the player registers a navigation callback:
   leaving the popout for another page carries the clip into this panel. A stand-in that does
   nothing is right here: what that callback decides is `Player.leaving`'s subject. */
vi.mock('$app/navigation', () => ({
	goto: (...args: unknown[]) => goto(...args),
	beforeNavigate: vi.fn(),
	/* The run the panel walks is the REAL one (see the `asset-view` stand-in below), and opening a
	   list writes an address. Nothing here reads it back. */
	pushState: vi.fn(),
	replaceState: vi.fn()
}));
vi.mock('$app/paths', () => ({ resolve: (path: string) => path }));

/* Nowhere in particular. What matters to the panel is only whether the SAME file is open at full
 * size, which is what makes it stand down. See the component. */
/** What `page.state` says. A test about leaving the full-size view sets it; every other test has
 *  nothing there, which is "no panel is open at full size". */
const pageState = vi.hoisted(() => ({ current: {} as Record<string, unknown> }));
vi.mock('$app/state', () => ({
	page: {
		get state() {
			return pageState.current;
		},
		get url() {
			return new URL('http://localhost/browse');
		},
		/* The screen the panel was opened over, which opening a file reads (a search's open, and
		   what a sitting was opened from): the library, as the address above says. */
		get route() {
			return { id: '/browse' };
		},
		get params() {
			return {};
		}
	}
}));

/* The RUN is real, and that is the point: the panel asks the same run the full-size view walks, and
   a stand-in answering Next and Back here would prove only that the panel calls something. Only
   handing a clip back to full size is stood in for, because that writes history. */
vi.mock('$lib/player/asset-view', async () => ({
	...(await vi.importActual<typeof import('$lib/player/asset-view')>('$lib/player/asset-view')),
	reopenAsset: vi.fn()
}));

/** What the server says about this file. A test that wants a conversion, a neighbour or a hidden
 *  file swaps the piece it cares about. */
const served = vi.hoisted(() => ({
	/** What `/assets/{id}` answers when the panel steps. */
	detail: {} as Record<string, unknown>,
	plan: {
		route: 'direct',
		reason: 'plays as it is',
		url: '/api/assets/asset-1/stream',
		scale_height: null as number | null,
		projected_realtime: null,
		streamable: true,
		duration_ms: 30_000,
		resume_ms: null
	}
}));

vi.mock('$lib/player/playback', async () => {
	const real = await vi.importActual<typeof import('$lib/player/playback')>('$lib/player/playback');
	return {
		...real,
		planFor: async () => served.plan,
		attach: () => ({ detach: vi.fn() }),
		startAt: () => null
	};
});

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async () => served.detail),
		post: vi.fn(async () => ({})),
		put: vi.fn(async () => ({})),
		// The phone's offer takes its quiet line back as a panel goes (`remote/offer`).
		del: vi.fn(async () => ({}))
	}
}));

vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettingValues: vi.fn(async () => new Map<string, unknown>()),
	saveSettings: vi.fn(async () => {}),
	onSettingsSaved: vi.fn()
}));

import MiniPlayer from './MiniPlayer.svelte';
import { handover, mini } from '$lib/player/mini.svelte';
import { api } from '$lib/api/client';
import { openAsset, reopenAsset, stepBack, stepForward } from '$lib/player/asset-view';
import { run } from '$lib/player/run.svelte';
import { dwell } from '$lib/player/dwell.svelte';
import { phoneWidth } from '$lib/components/common/phone-width.svelte';

/** The panel's source and its two children's, read as one: the rules these hold moved with the
 *  markup they style. */
const panelSource = (): string =>
	['MiniPlayer.svelte', 'MiniBar.svelte', 'MiniOverlay.svelte']
		.map((name) => readFileSync(join(dirname(fileURLToPath(import.meta.url)), name), 'utf8'))
		.join('\n');

const CLIP = (id: string) => ({ id, runs: true });

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

/* Taken down properly rather than merely removed: the panel listens on the WINDOW for the key that
   brings a clip back to full size, and a window listener outlives `host.remove()`. */
function takeDown() {
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
}

beforeEach(() => {
	vi.clearAllMocks();
	pageState.current = {};
	mini.close();
	/* Nowhere to step by default: a panel opened from a page of one draws no edge arrows. The run
	   is module state, so each test starts from a list of nothing rather than the last one's. */
	openAsset('nothing', []);
	run.shuffle = false;
});

afterEach(() => {
	takeDown();
	mini.close();
	served.plan = { ...served.plan, route: 'direct', reason: 'plays as it is' };
	served.detail = {};
});

/** The panel, with something in it. The plan is a promise, so two turns before anything is drawn. */
async function show(asset: { id: string; mediaType?: string; sprite?: null }) {
	takeDown();
	mini.open(asset, { width: 1400, height: 900 });
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(MiniPlayer, { target: host, props: {} });
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
	return host;
}

function button(label: string): HTMLElement | null {
	const glyph = host.querySelector(`[aria-label="${label}"]`);
	return (glyph as HTMLElement | null)?.closest('button') ?? null;
}

describe('a clip in the panel', () => {
	it('draws the timeline, and it is the one every other surface uses', async () => {
		await show({ id: 'asset-1', mediaType: 'video' });

		// `.timeline` is the shared scrubber's own box. A hand-drawn line in here would not have one.
		expect(host.querySelector('.timeline input[type="range"]')).not.toBeNull();
	});

	it('draws no player bar behind it', async () => {
		// A bar drawn and clipped away by the panel is one nobody can see and Tab still finds.
		await show({ id: 'asset-1', mediaType: 'video' });

		expect(host.querySelector('[aria-label="Playback controls"]')).toBeNull();
	});

	it('keeps the hairline the player itself draws', async () => {
		// Rather than a second one of its own. The panel places it; the player says how far along.
		await show({ id: 'asset-1', mediaType: 'video' });

		expect(host.querySelector('.player-progress')).not.toBeNull();
	});

	it('has a play control in the middle of the picture', async () => {
		await show({ id: 'asset-1', mediaType: 'video' });

		expect(button('Play')).not.toBeNull();
	});

	it('says why a file is being converted, as the mark the file page draws', async () => {
		/*
		 * The mark says why a file takes the path it takes, so a 4K file being reduced in the
		 * corner looks different from any other, as every picture says it.
		 */
		served.plan = {
			...served.plan,
			route: 'transcode',
			reason: 'Your browser cannot play VP9, so it is being converted as you watch.'
		};
		await show({ id: 'asset-1', mediaType: 'video' });

		expect(button('Why this file is being converted')).not.toBeNull();
		expect(host.textContent).toContain('being converted as you watch');
	});

	it('draws no mark on a file that plays as it is', async () => {
		await show({ id: 'asset-1', mediaType: 'video' });

		expect(button('Why this file is being converted')).toBeNull();
	});
});

describe('a picture in the panel', () => {
	it('draws the picture itself', async () => {
		await show({ id: 'asset-7', mediaType: 'image' });

		expect(host.querySelector('img')?.getAttribute('src')).toBe('/api/assets/asset-7/stream');
	});

	it('draws a picture for a GIF too', async () => {
		// A video element cannot decode one: no browser demuxes GIF through the media stack, so it
		// would show a blank frame for ever and report nothing wrong.
		await show({ id: 'asset-8', mediaType: 'gif' });

		expect(host.querySelector('img')).not.toBeNull();
		expect(host.querySelector('video')).toBeNull();
	});

	it('has no timeline and nothing to press', async () => {
		// There is no playhead to move and nothing to pause.
		await show({ id: 'asset-7', mediaType: 'image' });

		expect(host.querySelector('.timeline')).toBeNull();
		expect(button('Play')).toBeNull();
	});

	it('writes a view for a picture reached in the corner', async () => {
		// A photograph has no player to keep a sitting, so the corner keeps one for it.
		await show({ id: 'asset-7', mediaType: 'image' });

		const views = vi
			.mocked(api.post)
			.mock.calls.filter(([path]) => path === '/assets/asset-7/view');
		expect(views).toHaveLength(1);
		expect((views[0][1] as { body: Record<string, unknown> }).body).toMatchObject({
			screen: 'corner',
			watch_ms: 0
		});
	});

	it('carries the panel sitting on for a picture handed over, and reports it on the way out', async () => {
		const handed = {
			asset: 'asset-7',
			id: 'the-panel-sitting',
			from: performance.now(),
			sent: 0,
			place: { screen: 'panel' as const, opened_from: 'library' as const },
			magnified: false,
			filled: 0
		};
		takeDown();
		mini.open({ id: 'asset-7', mediaType: 'image', sitting: handed }, { width: 1400, height: 900 });
		host = document.createElement('div');
		document.body.append(host);
		mounted = mount(MiniPlayer, { target: host, props: {} });
		flushSync();
		// The view was earned in the panel: nothing more is said on arrival.
		expect(vi.mocked(api.post)).not.toHaveBeenCalled();

		takeDown();

		const views = vi
			.mocked(api.post)
			.mock.calls.filter(([path]) => path === '/assets/asset-7/view');
		expect(views).toHaveLength(1);
		expect((views[0][1] as { body: Record<string, unknown> }).body).toMatchObject({
			sitting: 'the-panel-sitting',
			already_reported_ms: 0,
			screen: 'panel'
		});
	});

	it('leaves the space bar alone', async () => {
		/* The panel binds Space to play and pause. With a photograph in it there is nothing to
		 * pause, and a key that is TAKEN and then does nothing is worse than one that is not
		 * bound, because whatever would have used it never sees the press. */
		await show({ id: 'asset-7', mediaType: 'image' });

		const press = new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true });
		window.dispatchEvent(press);

		expect(press.defaultPrevented).toBe(false);
	});

	it('answers the key that takes it back to full size', async () => {
		// The one key a picture in the panel does mean something to.
		await show({ id: 'asset-7', mediaType: 'image' });

		const press = new KeyboardEvent('keydown', { key: 'i', bubbles: true, cancelable: true });
		window.dispatchEvent(press);
		flushSync();

		expect(press.defaultPrevented).toBe(true);
	});
});

describe('the panel-s own chrome', () => {
	it('puts the way back at the far end from the way out', async () => {
		/* Opposites, so they are not a few pixels apart: one makes this bigger and the other throws
		 * it away. Read off the header in the order they are drawn. */
		await show({ id: 'asset-1', mediaType: 'video' });
		const labels = [...host.querySelectorAll('.grip button')].map((one) =>
			one.getAttribute('aria-label')
		);

		expect(labels[0]).toBe('Back to full size');
		expect(labels.at(-1)).toBe('Close');
	});

	it('says what it is holding rather than saying every file is playing', async () => {
		await show({ id: 'asset-1', mediaType: 'video' });
		expect(host.querySelector('.what')?.textContent).toBe('Playing');

		await show({ id: 'asset-7', mediaType: 'image' });
		// "Playing" over a photograph is a sentence that is not true.
		expect(host.querySelector('.what')?.textContent).toBe('Showing');
	});

	it('carries these controls and no others', async () => {
		/* The whole strip rather than its two ends, because what this is really guarding is a
		 * control that has GONE: a test pressing `button(...)?.click()` on a null and asserting the
		 * panel had not moved is true of a button that does not exist. A list is the only shape
		 * that notices a control appearing or leaving. */
		await show({ id: 'asset-1', mediaType: 'video' });

		const labels = [...host.querySelectorAll('.grip button')].map((one) =>
			one.getAttribute('aria-label')
		);

		expect(labels).toEqual(['Back to full size', 'Open audio player', 'Close']);
	});
});

describe('the audio-only bar', () => {
	it('is offered for a clip and not for a picture', async () => {
		await show({ id: 'asset-1', mediaType: 'video' });
		expect(button('Open audio player')).not.toBeNull();

		await show({ id: 'asset-7', mediaType: 'image' });
		expect(button('Open audio player')).toBeNull();
	});

	it('keeps the same player when the panel becomes the bar and back', async () => {
		await show({ id: 'asset-1', mediaType: 'video' });
		const video = host.querySelector('video');

		button('Open audio player')?.click();
		flushSync();

		expect(host.querySelector('section.mini')?.classList.contains('bar')).toBe(true);
		expect(host.querySelector('video'), 'the sound stopped with the picture').toBe(video);
		expect(button('Open mini player')).not.toBeNull();
		expect(button('Pause') ?? button('Play')).not.toBeNull();

		button('Open mini player')?.click();
		flushSync();

		expect(host.querySelector('section.mini')?.classList.contains('bar')).toBe(false);
		expect(host.querySelector('video')).toBe(video);
	});

	it('goes down to the bar on A and back up to the panel on the next A', async () => {
		await show({ id: 'asset-1', mediaType: 'video' });
		const video = host.querySelector('video');

		const down = new KeyboardEvent('keydown', { key: 'a', bubbles: true, cancelable: true });
		window.dispatchEvent(down);
		flushSync();
		expect(down.defaultPrevented).toBe(true);
		expect(mini.bar).toBe(true);
		expect(host.querySelector('video'), 'the sound stopped with the picture').toBe(video);

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'A', bubbles: true }));
		flushSync();
		expect(mini.bar).toBe(false);
		expect(mini.asset?.id).toBe('asset-1');
	});

	it('says what the mute key did in the corner, on the panel and on the Audio player', async () => {
		await show({ id: 'asset-1', mediaType: 'video' });
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'm', bubbles: true }));
		flushSync();
		expect(host.querySelector('.key-echoes [aria-label="Muted"]')).not.toBeNull();

		mini.toBar();
		flushSync();
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'm', bubbles: true }));
		flushSync();
		expect(host.querySelector('.key-echoes [aria-label="Sound on"]')).not.toBeNull();
	});

	it('leaves A alone on a picture, and with Ctrl held (that picks everything)', async () => {
		await show({ id: 'asset-7', mediaType: 'image' });
		const press = new KeyboardEvent('keydown', { key: 'a', bubbles: true, cancelable: true });
		window.dispatchEvent(press);
		expect(press.defaultPrevented).toBe(false);

		await show({ id: 'asset-1', mediaType: 'video' });
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', ctrlKey: true, bubbles: true }));
		flushSync();
		expect(mini.bar).toBe(false);
	});

	it('does not hold a picture as a bar', () => {
		mini.open({ id: 'asset-7', mediaType: 'image' }, { width: 1400, height: 900 }, { bar: true });
		expect(mini.bar).toBe(false);
	});
});

describe('the key badge on the audio-only bar', () => {
	it('stands on the strip-s small picture, never over the transport', () => {
		const source = panelSource();
		const picture = /\.mini\.bar \.screen \{\s*grid-area: (\w+);/.exec(source);
		const badge = /\.mini\.bar \.key-echoes \{\s*grid-area: (\w+);/.exec(source);
		const transport = /\.transport \{\s*grid-area: (\w+);/.exec(source);
		expect(picture).not.toBeNull();
		expect(transport).not.toBeNull();
		expect(badge?.[1]).toBe(picture?.[1]);
		expect(badge?.[1]).not.toBe(transport?.[1]);
	});
});

describe('docked above the tabs at a phone width', () => {
	/*
	 * A floating panel at 393x659 would stand over the tab bar, so the only way round the app would
	 * be under a picture nobody can drag clear with a finger.
	 */
	afterEach(() => {
		phoneWidth.yes = false;
	});

	it('is the strip, not the panel, with play and the two ways out a finger can hit', async () => {
		phoneWidth.yes = true;
		openAsset('asset-1', ['asset-0', 'asset-1', 'asset-2'].map(CLIP));
		await show({ id: 'asset-1', mediaType: 'video' });

		const panel = host.querySelector('section.mini');
		expect(panel?.classList.contains('docked')).toBe(true);
		expect(panel?.classList.contains('bar')).toBe(true);
		/* The strip's own controls. The panel's chrome is still in the document and the bar's rule
		   takes it off the screen, as it always has for the audio-only bar. */
		const strip = (label: string) =>
			host.querySelector(`.transport [aria-label="${label}"], .ends [aria-label="${label}"]`);
		expect(strip('Pause') ?? strip('Play')).not.toBeNull();
		expect(strip('Back to full size')).not.toBeNull();
		expect(strip('Close')).not.toBeNull();
		/* What does not fit a thumb across 390 pixels is the full-size view's there. */
		expect(strip('Previous')).toBeNull();
		expect(strip('Next')).toBeNull();
		expect(host.querySelectorAll('.transport button'), 'Play alone').toHaveLength(1);
		expect(strip('Open mini player')).toBeNull();
	});

	it('says its height while it is there, so the selection bar rises above it', async () => {
		phoneWidth.yes = true;
		await show({ id: 'asset-1', mediaType: 'video' });
		const root = document.documentElement;
		/* The strip's own height and a gap: the same number the strip stands at, so a strip that
		   grows (by its timeline, say) moves the selection bar with it. */
		const strip = (host.querySelector('section.mini') as HTMLElement).style.getPropertyValue(
			'--docked-strip'
		);
		expect(strip).toBe('calc(2 * var(--touch-target) + var(--space-2))');
		expect(root.style.getPropertyValue('--mini-docked')).toBe(`calc(${strip} + var(--space-2))`);

		takeDown();
		expect(root.style.getPropertyValue('--mini-docked')).toBe('');
	});

	it('has no play for a picture', async () => {
		phoneWidth.yes = true;
		await show({ id: 'asset-7', mediaType: 'image' });

		expect(host.querySelector('section.mini')?.classList.contains('docked')).toBe(true);
		expect(button('Pause') ?? button('Play')).toBeNull();
	});

	it("starts a picture's strip at the picture, with no empty column before it", async () => {
		/* An empty transport would take the first column and its gap, pushing the picture in and
		   leaving the name a sliver with the ways out across its end. */
		phoneWidth.yes = true;
		await show({ id: 'asset-7', mediaType: 'image' });

		const panel = host.querySelector('section.mini')!;
		expect(panel.querySelector('.transport'), 'an empty transport is still drawn').toBeNull();
		expect(panel.classList.contains('bare')).toBe(true);
	});

	it("keeps a clip's Play on its strip", async () => {
		phoneWidth.yes = true;
		await show({ id: 'asset-1', mediaType: 'video' });

		const panel = host.querySelector('section.mini')!;
		expect(panel.querySelector('.transport')).not.toBeNull();
		expect(panel.classList.contains('bare')).toBe(false);
	});

	it('lays the docked strip out in the bar order, the picture first and the name taking what is left', () => {
		/* The order every player bar draws: the scrub line along the top, then
		   the picture at the start, the name, Play, the ways out. */
		const source = panelSource().replace(/\s+/g, ' ');
		expect(source).toContain(
			"grid-template-columns: auto minmax(0, 1fr) auto auto; grid-template-areas: 'line line line line' 'picture title transport ends'; }"
		);
		expect(source).toContain(
			".mini.docked.bare { grid-template-columns: auto minmax(0, 1fr) auto; grid-template-areas: 'line line line' 'picture title ends'; }"
		);
	});

	it('stays the panel on a wide window', async () => {
		await show({ id: 'asset-1', mediaType: 'video' });

		expect(host.querySelector('section.mini')?.classList.contains('docked')).toBe(false);
		expect(document.documentElement.style.getPropertyValue('--mini-docked')).toBe('');
	});

	it('stands on the tab bar by the one height the tab bar is drawn at', () => {
		const source = panelSource();
		const docked = source.slice(source.indexOf('.mini.docked {'));
		expect(docked).toMatch(
			/^\.mini\.docked \{\s*inset-block-end: calc\(\s*var\(--tab-bar-height\) \+ var\(--safe-bottom\) \+ var\(--space-2\)/
		);
		/* And no number of its own for that height: a second copy is how the two would come to overlap. */
		expect(source).not.toMatch(/52px|53px/);
	});
});

describe('resizing the panel', () => {
	/* The panel keeps the shape of what is in it while it is dragged, and only when that shape is
	 * KNOWN. A file whose header the browser has not read yet has none, and neither does a Theater
	 * wall, which is several clips in a layout. Both fall through to a free resize rather than to a
	 * guess.
	 *
	 * The guard is `shapeOf`'s, and these are what exercise it.
	 */
	function drag(grip: string, byX: number, byY: number) {
		const corner = host.querySelector(`.corner.${grip}`) as HTMLElement;
		/* jsdom will not capture a pointer that was never really down, and the capture is the panel
		   keeping the gesture when the pointer outruns it, not what is under test here. */
		corner.setPointerCapture = () => {};
		corner.releasePointerCapture = () => {};
		corner.dispatchEvent(
			new PointerEvent('pointerdown', { button: 0, clientX: 0, clientY: 0, bubbles: true })
		);
		flushSync();
		corner.dispatchEvent(
			new PointerEvent('pointermove', { clientX: byX, clientY: byY, bubbles: true })
		);
		flushSync();
	}

	it('resizes freely while the shape of what it holds is not known', async () => {
		await show({ id: 'asset-1', mediaType: 'video' });
		const before = { ...mini.place };

		// The east edge only: an edge moves ONE dimension, so a height that moved with it is a shape
		// being kept, which is exactly what must not happen while there is no shape to keep.
		drag('e', -40, 30);

		expect(mini.place.width).toBe(before.width - 40);
		expect(mini.place.height).toBe(before.height);
	});

	it('keeps the shape once the browser has read it', async () => {
		await show({ id: 'asset-1', mediaType: 'video' });
		mini.shape = 2;
		const before = { ...mini.place };

		drag('e', -40, 0);

		expect(mini.place.width).toBe(before.width - 40);
		expect(mini.place.height).toBe((before.width - 40) / 2);
	});
});

describe('stepping onto a file that is hidden', () => {
	/*
	 * The panel walks the list it was opened from, and a hidden file sits in that list like any
	 * other. It comes back as the placeholder (`concealed` set, an empty media type, no art), and
	 * an empty media type must not be taken for a picture: that would point an `<img>` at a stream
	 * that refuses and replace the frame, the strip and both arrows with a paragraph about a
	 * missing drive, for a file that is where it always was.
	 */
	async function stepToHidden(): Promise<void> {
		// A file either side of the hidden one, so it has a way on in both directions.
		openAsset('asset-1', ['asset-0', 'asset-1', 'asset-9', 'asset-10'].map(CLIP));
		served.detail = { id: 'asset-9', media_type: '', concealed: true, added_at: 0 };
		await show({ id: 'asset-1', mediaType: 'video' });

		button('Next')?.click();
		await Promise.resolve();
		await Promise.resolve();
		await Promise.resolve();
		flushSync();
	}

	it('says so where the picture would be, rather than pointing a picture at bytes it cannot have', async () => {
		await stepToHidden();

		expect(host.textContent).toContain('This one is hidden');
		expect(host.querySelector('img')).toBeNull();
		expect(host.querySelector('video')).toBeNull();
	});

	it('keeps the panel, its strip and its size', async () => {
		const before = { ...mini.place };

		await stepToHidden();

		expect(host.querySelector('.mini')).not.toBeNull();
		expect(
			[...host.querySelectorAll('.grip button')].map((one) => one.getAttribute('aria-label'))
		).toEqual(['Back to full size', 'Close']);
		expect(host.querySelectorAll('.corner').length).toBe(8);
		expect(mini.place.width).toBe(before.width);
		expect(mini.place.height).toBe(before.height);
	});

	it('keeps the way on, which is the only way back off a hidden file', async () => {
		await stepToHidden();

		expect(button('Previous')).not.toBeNull();
		expect(button('Next')).not.toBeNull();
	});

	it('says Hidden rather than claiming to be showing something', async () => {
		await stepToHidden();

		expect(host.querySelector('.what')?.textContent).toBe('Hidden');
	});
});

/*
 * The repair mark does not sit on the close button.
 *
 * The header strip floats over the picture, so the picture's top right corner is the close button's
 * square, and both appear on the same wake.
 *
 * Pinned off the component's source rather than a rendered box: the placement is CSS, jsdom lays
 * nothing out, and the fault would be one rule losing its offset. Both halves are held: the mark
 * clears the strip, and the strip's height is named once so the two cannot drift apart.
 */
describe('the mark over the picture', () => {
	const source = panelSource();

	it('starts below the header strip rather than inside it', () => {
		/* The mark's own rule, not the whole file: the key echoes sit at the same offset, and a
		   search over the whole source would pass on theirs after the mark had lost its own. */
		const at = source.indexOf('\t.notice {\n');
		expect(at).toBeGreaterThan(-1);
		const rule = source.slice(at, source.indexOf('\n\t}', at));
		expect(rule).toContain('inset-block-start: calc(var(--grip-band, 28px) + var(--space-2));');
	});

	it('measures that strip in one place, which both rules read', () => {
		expect(source).toContain('--grip-band: 28px;');
		expect(source).toContain('block-size: var(--grip-band);');
	});
});

/*
 * THE PROGRESS LINE SITS ON THE BOTTOM EDGE, ALONG ITS STRAIGHT PART, IN BOTH FRAMES.
 *
 * A line lifted two pixels and inset by `--space-2` with rounded ends floats as a pill above the
 * edge; one run full width into the frame's curves is sliced by them. One placement:
 * `inset-block-end: 0` and `inset-inline: var(--progress-inset)`,
 * where each frame publishes its own corner as `--progress-inset`. One thickness, `--progress-line`,
 * read by the player's line and by the tile's.
 *
 * Pinned off the source for the same reason as the mark above: the placement is CSS, and jsdom lays
 * nothing out. Each rule is read on its own, so a literal inset written into one of them is caught
 * even while the other stays right.
 */
describe('the progress line along the bottom', () => {
	const read = (...parts: string[]) =>
		readFileSync(join(dirname(fileURLToPath(import.meta.url)), ...parts), 'utf8');
	const panel = panelSource();
	const stage = read('MediaStage.svelte');

	/** The body of the first rule whose selector line is exactly `selector {`. */
	const rule = (source: string, selector: string): string => {
		const start = source.indexOf(`\t${selector} {\n`);
		expect(start, `no rule for ${selector}`).toBeGreaterThanOrEqual(0);
		return source.slice(start, source.indexOf('\n\t}', start));
	};

	it.each([
		['the corner panel', panel, '.screen :global(.player-progress)'],
		['the full-size stage', stage, '.stage :global(.player-progress)']
	])('%s puts the line ON the edge and leaves its ends to the line', (_name, source, selector) => {
		const body = rule(source, selector);
		expect(body).toContain('inset-block-end: 0;');
		// The ends are the line's own arithmetic (below); a frame stating them again, by its radius
		// or by a spacing step, is how the line would stop short of the curves.
		expect(body).not.toContain('inset-inline');
		expect(body).not.toContain('var(--space-2)');
		expect(body).not.toMatch(/\binset:/);
		expect(body).not.toContain('border-radius');
	});

	it('works out its ends from the corner and its own thickness, where the top row meets the curve', () => {
		const line = rule(read('Player.svelte'), '.player-progress');
		expect(line).toContain('--corner: calc(var(--frame-corner, 0px) / 1px);');
		expect(line).toContain('--thickness: calc(var(--progress-line) / 1px);');
		// r - sqrt(r^2 - (r - h)^2): 3.4px for a 10px corner, 10.3px for 20px, 0 for a square frame.
		// And that is the only inset the rule sets: a plainer one written after it would win the
		// cascade and put the ends at the whole radius, with the arithmetic still on the page.
		expect(line.match(/inset-inline/g)).toHaveLength(1);
		const ends = line.slice(line.indexOf('inset-inline')).replace(/\s+/g, ' ');
		expect(ends).toContain('var(--corner) - sqrt(');
		expect(ends).toContain(
			'max( 0, var(--corner) * var(--corner) - (var(--corner) - var(--thickness))'
		);
	});

	it('has each frame publish the corner its rounding reads', () => {
		expect(rule(panel, '.mini')).toContain('--frame-corner: var(--radius-md);');
		expect(rule(panel, '.mini')).toContain('border-radius: var(--frame-corner);');
		expect(rule(stage, '.stage')).toContain(
			'--frame-corner: var(--stage-radius, var(--radius-xl));'
		);
		expect(rule(stage, '.stage')).toContain('border-radius: var(--frame-corner);');
	});

	it('keeps the corner panel timeline clear of the curves by that same corner', () => {
		expect(rule(panel, '.timeline-slot')).toContain(
			'inset-inline: var(--frame-corner, var(--radius-md));'
		);
	});

	it('draws the corner panel hairline as a ring inside the box, so the line can be the edge', () => {
		// A `border` sits outside the padding box the overflow clips to: the line could then only
		// ever float one pixel up from the edge with the hairline running under it.
		expect(rule(panel, '.mini')).not.toMatch(/\bborder:/);
		const ring = rule(panel, '.mini::after');
		expect(ring).toContain('border: 1px solid var(--sift-line);');
		expect(ring).toContain('pointer-events: none;');
	});

	it('draws the line at one thickness in the player and on a tile', () => {
		expect(rule(read('Player.svelte'), '.player-progress')).toContain(
			'block-size: var(--progress-line);'
		);
		expect(rule(read('..', 'Tile.svelte'), '.progress')).toContain(
			'block-size: var(--progress-line);'
		);
	});
});

/*
 * LEAVING THE FULL-SIZE VIEW ON A HAND-OVER.
 *
 * The panel fills, and the full-size view has to go: back out of the dialog, or to the library when
 * the file page was opened cold and has nothing behind it. The rule is the shell's own
 * (`leaveAssetPanel`), and these pin that the panel asks it rather than reading "is a panel open",
 * which a cold-opened file page answers yes to as well, and which would step back out of Sift.
 */
describe('handing a clip over from the full-size view', () => {
	async function handOver(state: Record<string, unknown>) {
		pageState.current = { asset: 'asset-1', ...state };
		takeDown();
		mini.open(
			{ id: 'asset-1', mediaType: 'video' },
			{ width: 1400, height: 900 },
			{ handover: true }
		);
		host = document.createElement('div');
		document.body.append(host);
		mounted = mount(MiniPlayer, { target: host, props: {} });
		await Promise.resolve();
		await Promise.resolve();
		flushSync();
	}

	it('leaves a page opened cold for the library rather than stepping back out of Sift', async () => {
		const back = vi.spyOn(window.history, 'back').mockImplementation(() => {});
		await handOver({ direct: true });
		expect(goto).toHaveBeenCalledWith('/browse');
		expect(back).not.toHaveBeenCalled();
		back.mockRestore();
	});

	it('steps back to the wall a dialog was opened over', async () => {
		const back = vi.spyOn(window.history, 'back').mockImplementation(() => {});
		await handOver({ direct: false });
		expect(back).toHaveBeenCalledTimes(1);
		expect(goto).not.toHaveBeenCalledWith('/browse');
		back.mockRestore();
	});
});

/*
 * THE CORNER WALKS THE SAME RUN THE FULL-SIZE VIEW DOES.
 *
 * Reading the list's own neighbours and nothing else would ignore Shuffle entirely: a clip handed
 * down from a shuffled run would step through the list in order, and Back would be the neighbour
 * rather than the file just watched. It asks the run, and these walk it from both ends.
 */
describe('the panel with Shuffle on', () => {
	/** Next or Previous on the panel, and the id it stepped to (the file it asked the server for). */
	async function press(label: 'Next' | 'Previous'): Promise<string> {
		const asked = vi.mocked(api.get);
		asked.mockClear();
		button(label)?.click();
		for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
		flushSync();
		const path = String(asked.mock.calls.at(-1)?.[0] ?? '');
		return path.replace('/assets/', '');
	}

	/* Every draw fixed, so the order is known and differs from the list's own: that is what makes a
	   step through the list and a step through the walk land on different files. */
	let draw: { mockRestore(): void } | null = null;
	function drawAlways(value: number): void {
		draw = vi.spyOn(Math, 'random').mockReturnValue(value);
	}

	afterEach(() => {
		draw?.mockRestore();
		draw = null;
		run.shuffle = false;
		run.reset();
	});

	it('steps to the file the shuffled walk names, and Back to the one just watched', async () => {
		// Draws of 0.99 leave the shared shuffle's order as it came, so the walk from asset-1 is
		// 0, 2, 3, 4, and the step after asset-0 is asset-2, where the list's own is asset-1.
		drawAlways(0.99);
		const list = ['asset-0', 'asset-1', 'asset-2', 'asset-3', 'asset-4'].map(CLIP);
		openAsset('asset-1', list);
		run.shuffle = true;
		// The walk, as the full-size view would take it: two steps from the file that was open.
		const first = (await stepForward('asset-1')) as string;
		const second = (await stepForward(first)) as string;
		expect(stepBack(second)).toBe(first);
		// Now handed down to the corner, standing on the first shuffled file.
		served.detail = { id: first, media_type: 'video', added_at: 0 };
		await show({ id: first, mediaType: 'video' });

		// Next is the walk's next, the same file the full-size view went to, not a new draw.
		expect(second).toBe('asset-2');
		expect(await press('Next')).toBe(second);
	});

	it('goes Back to the file the walk started on, not to the list neighbour', async () => {
		// Draws of 0 rotate the list, so the walk from asset-3 opens on asset-1, whose neighbour
		// in the list is asset-0, and whose place in the walk comes straight after asset-3.
		drawAlways(0);
		const list = ['asset-0', 'asset-1', 'asset-2', 'asset-3', 'asset-4'].map(CLIP);
		openAsset('asset-3', list);
		run.shuffle = true;
		const first = (await stepForward('asset-3')) as string;
		served.detail = { id: first, media_type: 'video', added_at: 0 };
		await show({ id: first, mediaType: 'video' });

		expect(first).toBe('asset-1');
		expect(button('Previous')).not.toBeNull();
		expect(await press('Previous')).toBe('asset-3');
	});

	it('draws no Back on the file a shuffled walk starts from', async () => {
		openAsset('asset-1', ['asset-0', 'asset-1', 'asset-2'].map(CLIP));
		run.shuffle = true;
		await show({ id: 'asset-1', mediaType: 'video' });

		// In order there would be a neighbour on each side; shuffled, nothing has been watched yet.
		expect(button('Previous')).toBeNull();
		expect(button('Next')).not.toBeNull();
	});
});

/*
 * THE TIMELINE ALONG THE AUDIO PLAYER'S FOOT.
 *
 * The bar draws the scrubber every other player draws, in a band under its row of controls: a
 * line along its foot that nobody can press, drag or reach by keyboard is the fault these hold.
 */
describe("the Audio player's timeline", () => {
	const source = panelSource();
	/** The body of the first rule whose selector line is exactly `selector {`. */
	const rule = (selector: string): string => {
		const start = source.indexOf(`\t${selector} {\n`);
		expect(start, `no rule for ${selector}`).toBeGreaterThanOrEqual(0);
		return source.slice(start, source.indexOf('\n\t}', start));
	};

	/** The bar, holding a clip whose length the browser has read. */
	async function bar(seconds = 60): Promise<HTMLVideoElement> {
		await show({ id: 'asset-1', mediaType: 'video' });
		button('Open audio player')?.click();
		flushSync();
		const video = host.querySelector('video') as HTMLVideoElement;
		Object.defineProperty(video, 'duration', { configurable: true, value: seconds });
		video.dispatchEvent(new Event('loadedmetadata'));
		flushSync();
		return video;
	}

	afterEach(() => {
		phoneWidth.yes = false;
	});

	const timeline = () =>
		host.querySelector('.bar-timeline .timeline input[type="range"]') as HTMLInputElement | null;

	it('draws the scrubber every player draws, and no line of its own', async () => {
		await bar();

		expect(timeline(), 'the bar has no timeline to press').not.toBeNull();
		expect(host.querySelector('.played')).toBeNull();
		// One timeline at a time: the panel's own is not mounted behind the bar.
		expect(host.querySelectorAll('input[type="range"][aria-label="Position"]')).toHaveLength(1);
	});

	it('moves the clip to where it is pressed or dragged', async () => {
		const video = await bar();

		const track = timeline()!;
		track.value = '12';
		track.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();

		expect(video.currentTime).toBe(12);
	});

	it('takes the arrow keys, five seconds a press and once', async () => {
		const video = await bar();
		video.currentTime = 20;
		video.dispatchEvent(new Event('timeupdate'));
		flushSync();

		const track = timeline()!;
		track.focus();
		track.dispatchEvent(
			new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true, cancelable: true })
		);
		flushSync();

		expect(video.currentTime).toBe(15);
	});

	it('says where the clip is at the scrub line-s start and its length at its end', async () => {
		const video = await bar(125);
		video.currentTime = 62;
		video.dispatchEvent(new Event('timeupdate'));
		flushSync();

		expect(host.querySelector('.bar-timeline .time.start')?.textContent).toBe('1:02');
		expect(host.querySelector('.bar-timeline .time.end')?.textContent).toBe('2:05');
		expect(host.querySelector('.named .time'), 'the clock is still under the name').toBeNull();
	});

	it('is drawn and dimmed on a picture, which has no playhead', async () => {
		phoneWidth.yes = true;
		await show({ id: 'asset-7', mediaType: 'image' });

		expect(timeline()?.disabled).toBe(true);
		expect(host.querySelector('.bar-timeline .time')).toBeNull();
	});

	it("lies along the top, in Theater's bar's shell, tall by its content", () => {
		const floating = rule('.mini.bar').replace(/\s+/g, ' ');
		expect(floating).toContain('block-size: auto;');
		expect(floating).toContain('padding: var(--space-2) var(--space-3);');
		expect(floating).toContain('grid-template-rows: auto auto;');
		expect(floating).toContain("grid-template-areas: 'line line line' 'transport start ends';");
		expect(floating).toContain('border: 1px solid var(--sift-line);');
		expect(floating).toContain('background: var(--sift-scrim);');
		expect(floating).toContain('box-shadow: none;');
		expect(rule('.bar-timeline')).toContain('grid-area: line;');
	});

	it('is a finger high on the docked strip, which grows by it', () => {
		/* The slider's own box is a finger high at a phone's width (`Slider`); the strip keeps a
		   band that tall over its controls, so the target lies under no press. */
		const strip = rule('.mini.docked');
		expect(strip).toContain('block-size: var(--docked-strip);');
		expect(strip).toContain('grid-template-rows: var(--touch-target) minmax(0, 1fr);');
	});

	it('shows the frame under the pointer above the bar, clear of the name, with no lift', () => {
		/* The timeline is the bar's top row, so the frame the gap above its own top edge stands
		   above the bar, over nothing of it: no lift is set. */
		expect(panelSource()).not.toContain('--scrub-preview-lift');
		const preview = readFileSync(
			join(dirname(fileURLToPath(import.meta.url)), 'ScrubPreview.svelte'),
			'utf8'
		);
		expect(preview).toContain('inset-block-end: calc(100% + var(--space-2));');
		expect(preview).not.toContain('--scrub-preview-lift');
	});
});

describe('the audio-only bar keeps one shape', () => {
	/** The bar, holding the middle clip of three, as the Open audio player press leaves it. */
	async function bar(): Promise<void> {
		openAsset('asset-1', ['asset-0', 'asset-1', 'asset-2'].map(CLIP));
		await show({ id: 'asset-1', mediaType: 'video' });
		button('Open audio player')?.click();
		flushSync();
	}

	/** Every press on the bar, in the order drawn, and whether each one can act. */
	function presses(): string[] {
		return [...host.querySelectorAll('.transport button, .ends button')].map(
			(one) =>
				`${one.getAttribute('aria-label')}${(one as HTMLButtonElement).disabled ? ' (dimmed)' : ''}`
		);
	}

	afterEach(() => {
		run.shuffle = false;
		run.reset();
	});

	it('draws the same presses before and after Shuffle, dimming the one that cannot act', async () => {
		/* Previous leaving the moment Shuffle is pressed (a shuffled walk has nothing behind it yet)
		   would slide the whole row sideways under the pointer. */
		await bar();
		const before = presses();

		button('Shuffle')?.click();
		flushSync();
		const after = presses();

		expect(after.length).toBe(before.length);
		/* Repeat first, then the step pair, as on every player bar. */
		expect(before.slice(0, 2)).toEqual(['Play through', 'Previous']);
		expect(after[1]).toBe('Nothing before this (dimmed)');
		expect([after[0], ...after.slice(2)]).toEqual([before[0], ...before.slice(2)]);
	});

	it('presses what happens at the end into the one answer every player reads', async () => {
		/* The bar's control does not read the clip player's handle (dimmed whenever there was none):
		   it reads and writes `dwell.mode`, the answer a picture's drawer and the full-size player
		   read too. */
		await bar();
		const control = button('Play through') as HTMLButtonElement;
		expect(control.disabled).toBe(false);

		control.click();
		flushSync();

		expect(dwell.mode).toBe('loop_one');
		expect(button('Repeat this')).not.toBeNull();
		dwell.mode = 'loop_all';
	});

	it('mutes and unmutes on M', async () => {
		await bar();
		expect(button('Mute')).not.toBeNull();

		const down = new KeyboardEvent('keydown', { key: 'm', bubbles: true, cancelable: true });
		window.dispatchEvent(down);
		flushSync();
		expect(down.defaultPrevented).toBe(true);
		expect(button('Unmute')).not.toBeNull();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'm', bubbles: true }));
		flushSync();
		expect(button('Mute')).not.toBeNull();
	});

	it('goes up to the panel on I, and the panel to full size on the next I', async () => {
		await bar();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'i', bubbles: true }));
		flushSync();
		expect(mini.bar).toBe(false);
		expect(mini.asset?.id).toBe('asset-1');
		expect(reopenAsset).not.toHaveBeenCalled();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'i', bubbles: true }));
		flushSync();
		expect(reopenAsset).toHaveBeenCalledWith('asset-1', undefined);
	});

	it('goes to full size filling the screen on F, from the bar and from the panel', async () => {
		await bar();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', bubbles: true }));
		flushSync();
		expect(reopenAsset).toHaveBeenCalledWith('asset-1', undefined);
		expect(handover.takeFill('asset-1')).toBe(true);

		vi.mocked(reopenAsset).mockClear();
		await show({ id: 'asset-1', mediaType: 'video' });
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'F', bubbles: true }));
		flushSync();
		expect(reopenAsset).toHaveBeenCalledWith('asset-1', undefined);
		expect(handover.takeFill('asset-1')).toBe(true);
	});

	it('leaves F to the page when the panel holds a picture', async () => {
		await show({ id: 'asset-7', mediaType: 'image' });

		const press = new KeyboardEvent('keydown', { key: 'f', bubbles: true, cancelable: true });
		window.dispatchEvent(press);

		expect(press.defaultPrevented).toBe(false);
		expect(handover.takeFill('asset-7')).toBe(false);
	});
});

describe('the corner at the end of a clip', () => {
	afterEach(() => run.reset());

	it('moves the run on to the next file, as the popout does', async () => {
		openAsset('asset-1', ['asset-0', 'asset-1', 'asset-2'].map(CLIP));
		served.detail = { id: 'asset-2', media_type: 'video', added_at: 0 };
		await show({ id: 'asset-1', mediaType: 'video' });
		const asked = vi.mocked(api.get);
		asked.mockClear();

		host.querySelector('video')!.dispatchEvent(new Event('ended'));
		for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
		flushSync();

		expect(asked.mock.calls.map(([path]) => String(path))).toContain('/assets/asset-2');
		expect(mini.asset?.id).toBe('asset-2');
	});
	it('finds the next file while this one plays, and steps to it without asking again', async () => {
		openAsset('asset-1', ['asset-0', 'asset-1', 'asset-2'].map(CLIP));
		served.detail = { id: 'asset-2', media_type: 'video', added_at: 0 };
		await show({ id: 'asset-1', mediaType: 'video' });
		const asked = vi.mocked(api.get);
		asked.mockClear();
		const video = host.querySelector('video')!;

		video.dispatchEvent(new Event('playing'));
		for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
		expect(asked).toHaveBeenCalledWith('/assets/asset-2');

		video.dispatchEvent(new Event('ended'));
		for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
		flushSync();
		expect(mini.asset?.id).toBe('asset-2');
		const records = asked.mock.calls.filter(([path]) => path === '/assets/asset-2');
		expect(records).toHaveLength(1);
	});
});
