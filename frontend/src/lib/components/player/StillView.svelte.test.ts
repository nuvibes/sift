/* The chrome a photograph gets.
 *
 * Two faults, one cause, if a still had no chrome of its own. Opening a picture from the grid would
 * offer no way to fill the screen with it, because the fullscreen button is on the player's bar and
 * a still has no player. And stepping onto a picture in the middle of a fullscreen run would take
 * that button away mid-run: the stage keeps fullscreen across the step, and somebody would be left
 * looking at a full screen with no visible way back out.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import StillHarness from './StillHarness.svelte';
import { forgetHeic } from './heic';
import { mini } from '$lib/player/mini.svelte';
import { noServerAt } from '../../../test-setup';

/* Left unanswered on purpose: the settings, read on the way past. */
noServerAt('/api/settings');

/* The frame driver, recorded rather than run: whether the still view starts one, holds it and lets
   it go is the question here, and decoding frames is `$lib/player/animation`'s own tested job. */
const driven: { held: boolean; played: number; paused: number; closed: number }[] = [];
/* The REAL module underneath, with only the two moving parts stood in for. `ANIMATION_UNHELD` comes
   through untouched, so the test that asserts the sentence is asserting the shipped one: a mock
   that listed its own exports would have made a copy of the copy it exists to prevent. */
vi.mock('$lib/player/animation', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/player/animation')>()),
	canDriveAnimations: () =>
		typeof (globalThis as { ImageDecoder?: unknown }).ImageDecoder !== 'undefined',
	driveAnimation: async (_url: string, _canvas: HTMLCanvasElement, options: { held?: boolean }) => {
		const record = { held: options.held ?? false, played: 0, paused: 0, closed: 0 };
		driven.push(record);
		return {
			play: () => (record.played += 1),
			pause: () => (record.paused += 1),
			close: () => (record.closed += 1)
		};
	}
}));

let host: HTMLElement;
/* Taken down properly rather than merely removed from the page.
 *
 * This view listens on the WINDOW for the key that sends a picture to the corner, and a window
 * listener outlives `host.remove()`: the element goes and the component's teardown never runs. One
 * test's picture then answers the next test's key press, and the failure lands somewhere that has
 * nothing to do with its cause. */
let mounted: Record<string, unknown> | null = null;

/** Take down whatever is on screen, listeners and all. */
function takeDown() {
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
}

afterEach(() => {
	takeDown();
	/* The panel is one object for the whole app, so a picture left in it by one test is still there
	   for the next, which is the application's own behaviour and a false pass here. */
	mini.close();
	vi.restoreAllMocks();
	/* And put the document back. `restoreAllMocks` undoes spies, not a redefined property, so a test
	 * that says "we are fullscreen" leaves every test after it believing that, against an element
	 * that has since been removed from the page. The failure it causes is in a LATER test and looks
	 * nothing like its cause, which is the worst kind there is. */
	Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: null });
});

function render(
	id = 'asset-1',
	props: {
		mediaType?: string;
		compact?: boolean;
		onprevious?: () => void;
		onnext?: () => void;
		mayNotDraw?: boolean;
	} = {}
) {
	// Whatever was here goes first, so a picture left listening on the window from an earlier
	// render cannot answer the next test's key press.
	takeDown();
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(StillHarness, { target: host, props: { id, ...props } });
	// `mount` returns before the mount effects have run, so the element the stage binds to itself is
	// still null until this. In a browser the two are never observed apart; in a test they are.
	flushSync();
	return host;
}

/** jsdom implements neither half of the fullscreen API, so both ends are stood in for. */
function fullscreenSpies() {
	const request = vi.fn();
	const exit = vi.fn();
	(host.querySelector('.stage') as HTMLElement).requestFullscreen = request;
	document.exitFullscreen = exit;
	return { request, exit };
}

describe('the picture', () => {
	it('is the file itself, not the tile-sized still', () => {
		// The thumbnail is made for a tile. Blown up to fill the stage it is a soft, blocky version
		// of a picture the person already has at full size.
		render('asset-9');

		expect(host.querySelector('img')?.getAttribute('src')).toBe('/api/assets/asset-9/stream');
	});

	it('goes full screen on a double-click, the same gesture the video takes', () => {
		render();
		const { request } = fullscreenSpies();

		host.querySelector('img')?.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));

		expect(request).toHaveBeenCalled();
	});

	it('and comes back out of it on a second double-click', () => {
		/* Asked of the document rather than of a flag, because fullscreen can also be left by Escape
		 * and by the browser, neither of which passes through the toggle. */
		render();
		const { request, exit } = fullscreenSpies();
		Object.defineProperty(document, 'fullscreenElement', {
			configurable: true,
			value: host.querySelector('.stage')
		});

		host.querySelector('img')?.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));

		expect(exit).toHaveBeenCalled();
		expect(request).not.toHaveBeenCalled();
	});
});

describe('the control', () => {
	it('carries a fullscreen button, so the gesture is not the only way in', () => {
		// A double-click nobody is told about is not an affordance. The button is what says the
		// picture can fill the screen; the gesture is the shortcut for people who already know.
		render();

		expect(host.querySelector('[aria-label="Full screen"]')).not.toBeNull();
	});

	it('wears the player-s bar, placed and faded by the stage', () => {
		/*
		 * A still wears `player-bar`: one shape on every bar, whatever is above it, as a theater
		 * cell draws under a photograph, so stepping from a clip onto a picture leaves every
		 * control where the hand left it.
		 */
		render();

		const bar = host.querySelector('.player-bar');
		expect(bar).not.toBeNull();
		expect(bar?.closest('.stage')).not.toBeNull();
		expect(
			host.querySelector('.still-control'),
			'a still is drawing a strip of its own'
		).toBeNull();
	});

	it('steps the list from the bar, exactly as a clip does', () => {
		/* A picture is a file in a list like any other, and the pair either side of Play is how that
		 * list is walked. Handed nothing there is nothing to step to and the buttons are not drawn,
		 * which is the bar's own rule, and is why they are asserted absent as well. */
		const back = vi.fn();
		const on = vi.fn();
		render('asset-1', { onprevious: back, onnext: on });

		(host.querySelector('[aria-label="Previous"]') as HTMLElement).closest('button')!.click();
		(host.querySelector('[aria-label="Next"]') as HTMLElement).closest('button')!.click();

		expect(back).toHaveBeenCalledTimes(1);
		expect(on).toHaveBeenCalledTimes(1);

		render('asset-1');
		expect(host.querySelector('[aria-label="Previous"]')).toBeNull();
		expect(host.querySelector('[aria-label="Next"]')).toBeNull();
	});

	it('draws no clock, because a picture has no playhead and no length', () => {
		// `0:00 / 0:00` under every photograph would be a readout saying nothing twice.
		render();

		expect(host.querySelector('.player-bar .time')).toBeNull();
	});

	it('keeps the sound control, dimmed, because a file with sound would light it', () => {
		// The difference from the two above: this one CAN apply to the next file in the run, so it
		// is dimmed rather than taken away.
		render();

		const mute = host.querySelector('[aria-label="No sound in this"]') as HTMLElement;
		expect((mute.closest('button') as HTMLButtonElement).disabled).toBe(true);
	});

	it('takes the button through the stage, so it survives being stepped onto in full screen', () => {
		// The second fault. The button has to come from the frame that is holding fullscreen,
		// not from the media that was just replaced.
		render();
		const { request } = fullscreenSpies();

		(host.querySelector('.player-bar [aria-label*="screen" i]') as HTMLElement)
			.closest('button')!
			.click();

		expect(request).toHaveBeenCalled();
	});
});

describe('looking closer, while fullscreen', () => {
	/* The step is held for a quarter of a second to see whether a second click is coming, so this
	   suite drives the clock rather than waiting on it. Restored in the file's own `afterEach`
	   below via `useRealTimers`, because fake timers left installed reach every test after these. */
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	/** Say the stage is the fullscreen element, and let the listener that watches for it run. */
	function goFullscreen() {
		Object.defineProperty(document, 'fullscreenElement', {
			configurable: true,
			value: host.querySelector('.stage')
		});
		document.dispatchEvent(new Event('fullscreenchange'));
		flushSync();
	}

	function picture() {
		return host.querySelector('img') as HTMLImageElement;
	}

	function wheel(deltaY: number, at = { clientX: 0, clientY: 0 }) {
		picture().dispatchEvent(
			new WheelEvent('wheel', { deltaY, ...at, bubbles: true, cancelable: true })
		);
		flushSync();
	}

	/**
	 * One press on the picture, the gesture that steps the magnification. The step waits to find
	 * out whether it was the first half of a double-press, so the timers must be run out for it to
	 * happen at all; that wait is why a double-click to leave the filled screen shows no zoom
	 * flash. See `StillView`.
	 */
	function press(at = { clientX: 0, clientY: 0 }) {
		picture().dispatchEvent(new MouseEvent('click', { ...at, bubbles: true }));
		vi.advanceTimersByTime(400);
		flushSync();
	}

	it('does nothing to a picture in the window, where a wheel already scrolls the page', () => {
		render();

		wheel(-300);

		expect(picture().style.scale).toBe('');
	});

	it('zooms in on a wheel once the picture has the screen to itself', () => {
		render();
		goFullscreen();

		wheel(-300);

		expect(Number(picture().style.scale)).toBeGreaterThan(1);
	});

	it('and will not zoom out past fitting the screen', () => {
		render();
		goFullscreen();

		wheel(2000);

		// Back at 1, and no offset left over: a picture that fits and sits off to one side is a picture
		// somebody has to drag back for no reason.
		expect(picture().style.scale).toBe('');
		expect(picture().style.translate).toBe('');
	});

	it('keeps the point under the pointer under the pointer', () => {
		/* Scaling about the centre is the version that feels broken: what somebody is pointing at
		 * slides away from the cursor as it grows, so zooming in on a face means zooming and then
		 * hunting for it. */
		render();
		goFullscreen();
		// jsdom gives every element a zero-sized box, so the picture's middle is at 0,0, which makes
		// a pointer at 100,50 exactly 100,50 away from it, and the correction easy to state.
		wheel(-300, { clientX: 100, clientY: 50 });

		const [x, y] = picture().style.translate.split(' ').map(parseFloat);
		// Away from the pointer, in both axes, because the picture grew under it.
		expect(x).toBeLessThan(0);
		expect(y).toBeLessThan(0);
	});

	it('pans on a drag once zoomed, and not before', () => {
		render();
		goFullscreen();

		// At fit-to-screen there is nothing to move, so a drag is not a pan.
		picture().dispatchEvent(
			new PointerEvent('pointerdown', { button: 0, clientX: 0, clientY: 0, bubbles: true })
		);
		picture().dispatchEvent(
			new PointerEvent('pointermove', { clientX: 40, clientY: 0, bubbles: true })
		);
		flushSync();
		expect(picture().style.translate).toBe('');

		wheel(-300);
		const before = picture().style.translate;

		picture().dispatchEvent(
			new PointerEvent('pointerdown', { button: 0, clientX: 0, clientY: 0, bubbles: true })
		);
		picture().dispatchEvent(
			new PointerEvent('pointermove', { clientX: 40, clientY: 25, bubbles: true })
		);
		flushSync();

		expect(picture().style.translate).not.toBe(before);
	});

	it('says with the pointer what can be done to it', () => {
		/*
		 * One cursor rule for every surface that magnifies a picture (see `Zoomable`), including a
		 * closed hand while the picture is being dragged.
		 */
		render();

		// In the WINDOW the wheel does nothing here, so nothing is offered: the picture keeps the
		// cursor that says it can be pressed. A magnifier over a surface that will not magnify is
		// worse than no magnifier at all.
		expect(picture().style.cursor).toBe('');

		goFullscreen();

		// Fullscreen, at fit-to-screen: the magnifying glass with a plus in it. Magnifying is
		// otherwise an undiscoverable gesture: the wheel does something here and nothing one pixel
		// outside, and there is nothing else on screen that says so.
		expect(picture().style.cursor).toBe('zoom-in');

		wheel(-300);
		/*
		 * Still a magnifier once the picture is magnified, with the sign turned over, so the press
		 * that takes the picture back to whole is still announced. `zoom-out` because that is what
		 * a press now does; a plus would promise the opposite of the gesture under it.
		 */
		expect(picture().style.cursor).toBe('zoom-out');

		picture().dispatchEvent(
			new PointerEvent('pointerdown', { button: 0, clientX: 0, clientY: 0, bubbles: true })
		);
		flushSync();
		// The one exception, and it is about a gesture in progress rather than about an offer: while
		// the button is down the picture is being MOVED, and that is worth saying.
		expect(picture().style.cursor).toBe('grabbing');

		picture().dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
		flushSync();
		expect(picture().style.cursor).toBe('zoom-out');
	});

	it('takes one press in, and the next one all the way back out', () => {
		/* A toggle rather than a ladder. Four presses in and a fifth back out would make the way out
		 * of a magnified picture cost four more presses and four more animations, through two
		 * magnifications nobody chose. The wheel is the instrument for arriving at 3.4x; a press is
		 * the coarse one, for somebody who wants to see a face and then wants the picture back. */
		render();
		goFullscreen();
		expect(picture().style.scale).toBe('');

		press();
		// A quarter of the way in: the maximum is 8.
		expect(picture().style.scale).toBe('2');

		press();
		expect(picture().style.scale).toBe('');
	});

	it('holds the press back for a real wait, not merely for a turn of the clock', () => {
		/*
		 * The wait is pinned by advancing the clock by less than it. At zero the step is due
		 * immediately and any advance runs it; at 260 nothing is due yet, and the picture is still
		 * whole. A test advancing past the wait would pass for any positive number.
		 *
		 * Both halves are needed: the first says nothing fired early, the second that something
		 * fires at all, or the test would pass on a press that never works.
		 */
		render();
		goFullscreen();

		picture().dispatchEvent(new MouseEvent('click', { clientX: 0, clientY: 0, bubbles: true }));
		vi.advanceTimersByTime(200);
		flushSync();
		expect(picture().style.scale, 'the step ran before the double-click window closed').toBe('');

		vi.advanceTimersByTime(200);
		flushSync();
		expect(picture().style.scale).toBe('2');
	});

	it('comes back to whole from any magnification, not only from the top', () => {
		/* The wheel can leave the picture anywhere, and the way back must not depend on where. This
		 * is the gesture somebody reaches for when they have lost the picture entirely. */
		render();
		goFullscreen();
		wheel(-300);
		const magnified = picture().style.scale;
		expect(magnified).not.toBe('');
		expect(magnified).not.toBe('2');

		press();
		expect(picture().style.scale).toBe('');
	});

	it('starts again at fitting when the next picture arrives', () => {
		// Somebody stepping through a run expects each one to arrive whole, not at the magnification
		// the last one was left at.
		render('asset-1');
		goFullscreen();
		wheel(-300);
		expect(picture().style.scale).not.toBe('');

		render('asset-2');
		goFullscreen();

		expect(picture().style.scale).toBe('');
	});

	it('and will not be panned off into empty space', () => {
		/*
		 * Panning is clamped to the picture's edge, which also keeps zooming out from an off-centre
		 * picture settling rather than drifting. jsdom measures every box as zero, so the picture
		 * is given a size here.
		 */
		render();
		goFullscreen();
		picture().getBoundingClientRect = () =>
			({ left: 0, top: 0, width: 400, height: 300 }) as DOMRect;

		wheel(-300); // about 1.8x, so the overhang is a little under half the box in each direction
		picture().dispatchEvent(
			new PointerEvent('pointerdown', { button: 0, clientX: 0, clientY: 0, bubbles: true })
		);
		// Far further than there is picture to see.
		picture().dispatchEvent(
			new PointerEvent('pointermove', { clientX: 5000, clientY: 5000, bubbles: true })
		);
		flushSync();

		const [x, y] = picture().style.translate.split(' ').map(parseFloat);
		// Bounded by the overhang rather than by what was asked for. The rect is the SCALED size here,
		// so half of it is the outer limit whatever the zoom came out at.
		expect(x).toBeLessThanOrEqual(200);
		expect(y).toBeLessThanOrEqual(150);
	});

	it('and moves further per pixel the more it is magnified', () => {
		/*
		 * One screen pixel of pointer moves the picture by more than one pixel: at eight times,
		 * crossing the whole picture pixel for pixel would take seven full drags, and at that
		 * magnification somebody is looking around.
		 */
		render();
		goFullscreen();
		/* The stub has to GROW with the zoom, the way a real box does. Fixed, the bound the pan is
		   clamped against shrinks as the magnification rises, which is the opposite of the truth. */
		picture().getBoundingClientRect = () => {
			const scale = Number(picture().style.scale || 1);
			return { left: 0, top: 0, width: 400 * scale, height: 300 * scale } as DOMRect;
		};

		/** Where the picture sits on screen. The style holds the offset in the picture's own unscaled
		 *  units (it is applied before the scale) so the visible position is that times the zoom. */
		const at = () => {
			const written = parseFloat(picture().style.translate.split(' ')[0] || '0');
			return written * Number(picture().style.scale || 1);
		};

		/**
		 * How far a small drag actually moves it, measured as a difference, so a starting position
		 * already against the clamp cannot be mistaken for a long drag.
		 */
		const moved = (by: number) => {
			const before = at();
			picture().dispatchEvent(
				new PointerEvent('pointerdown', { button: 0, clientX: 0, clientY: 0, bubbles: true })
			);
			picture().dispatchEvent(
				new PointerEvent('pointermove', { clientX: by, clientY: 0, bubbles: true })
			);
			picture().dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
			flushSync();
			return at() - before;
		};

		wheel(-300); // a little under 2x
		const gentle = moved(10);

		wheel(-2000); // as far in as it goes
		const hard = moved(10);

		// The same ten pixels of pointer, measurably further across the picture.
		expect(hard).toBeGreaterThan(gentle * 1.5);
	});
	/*
	 * Getting OUT, which is the gesture the two above could quietly take away.
	 *
	 * A double-click is two clicks and then a `dblclick`, and on this picture a click steps the
	 * magnification. Unguarded, double-clicking to leave the filled screen would zoom in, zoom out,
	 * and then (because the picture is magnified by an odd number of steps as often as not) reset
	 * the zoom and RETURN, leaving the screen exactly as full as it was.
	 */
	describe('and getting back out', () => {
		/** A double-click as a browser sends one: two clicks, each carrying its number. */
		function doubleClick(at = { clientX: 0, clientY: 0 }) {
			picture().dispatchEvent(new MouseEvent('click', { ...at, detail: 1, bubbles: true }));
			picture().dispatchEvent(new MouseEvent('click', { ...at, detail: 2, bubbles: true }));
			picture().dispatchEvent(new MouseEvent('dblclick', { ...at, detail: 2, bubbles: true }));
			flushSync();
		}

		it('leaves the filled screen, whatever the picture was doing', () => {
			render();
			goFullscreen();
			const { exit } = fullscreenSpies();

			doubleClick();

			expect(exit).toHaveBeenCalled();
		});

		it('and leaves the magnification where it was, rather than stepping it on the way', () => {
			render();
			goFullscreen();
			fullscreenSpies();
			// Taken somewhere with the wheel, which is the fine control. Whatever the way out does,
			// it must not be to move this.
			wheel(-300);
			const chosen = picture().style.scale;
			expect(chosen).not.toBe('');

			doubleClick();

			expect(picture().style.scale).toBe(chosen);
		});

		it('never zooms at all on a QUICK double-click, so nothing flashes', () => {
			/*
			 * Double-clicking to leave fullscreen must not briefly zoom in first. Taking the step
			 * back afterwards is not enough, because the step is animated over a third of a second;
			 * the press waits instead.
			 */
			render();
			goFullscreen();
			fullscreenSpies();

			// The two clicks arrive close together, and NO time is run out between them.
			picture().dispatchEvent(new MouseEvent('click', { detail: 1, bubbles: true }));
			flushSync();
			expect(picture().style.scale, 'it zoomed before it knew the click was single').toBe('');

			picture().dispatchEvent(new MouseEvent('click', { detail: 2, bubbles: true }));
			picture().dispatchEvent(new MouseEvent('dblclick', { detail: 2, bubbles: true }));
			// And the held press must not fire afterwards either.
			vi.advanceTimersByTime(1000);
			flushSync();

			expect(picture().style.scale).toBe('');
		});

		it('and a picture that was whole is still whole', () => {
			render();
			goFullscreen();
			fullscreenSpies();

			doubleClick();

			// The first of the two clicks stepped it. The step is taken straight back rather than
			// eased back, so nothing of it survives the gesture.
			expect(picture().style.scale).toBe('');
		});
	});
});

describe('carrying on with it in the corner', () => {
	/*
	 * A photograph goes to the small panel as a clip does, although the PLAYER does not draw it: a
	 * run of mixed media that lost the panel at every picture in it would lose it at the exact
	 * moment somebody goes looking for the next thing.
	 */

	it('offers the same control a clip does', () => {
		render();

		expect(host.querySelector('[aria-label="Open mini player"]')).not.toBeNull();
	});

	it('puts the picture in the panel', () => {
		render('asset-9', { mediaType: 'gif' });

		(host.querySelector('[aria-label="Open mini player"]') as HTMLElement).click();
		flushSync();

		expect(mini.asset?.id).toBe('asset-9');
		// What it IS travels with it. Without this the panel draws a video element pointed at a GIF,
		// which shows a blank frame for ever and reports nothing wrong.
		expect(mini.asset?.mediaType).toBe('gif');
	});

	it('leaves the full-size view behind, the way a clip does', () => {
		render();

		(host.querySelector('[aria-label="Open mini player"]') as HTMLElement).click();
		flushSync();

		// The same file in both places is one picture drawn twice, with two sets of controls.
		expect(mini.handover).toBe(true);
	});

	it('answers the same key', () => {
		render('asset-4');

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'i', bubbles: true }));
		flushSync();

		expect(mini.asset?.id).toBe('asset-4');
	});

	it('draws nothing over the picture once it IS the panel', () => {
		// The panel is a few hundred pixels across and draws its own controls in the middle of the
		// picture. A second set along the bottom would be most of what there is to look at.
		render('asset-1', { compact: true });

		expect(host.querySelector('[aria-label="Picture controls"]')).toBeNull();
		expect(host.querySelector('[aria-label="Open mini player"]')).toBeNull();
	});

	it('does not take the key back off the panel', () => {
		// In the panel that key means "back to full size", and the panel binds it. A picture that
		// answered it too would put itself in the panel it is already in.
		render('asset-1', { compact: true });

		const press = new KeyboardEvent('keydown', { key: 'i', bubbles: true, cancelable: true });
		window.dispatchEvent(press);
		flushSync();

		// Not acted on, and, the half that matters, not swallowed either: a key that is taken
		// and then does nothing never reaches whatever would have used it.
		expect(mini.asset).toBeNull();
		expect(press.defaultPrevented).toBe(false);
	});
});

describe('a GIF', () => {
	beforeEach(() => {
		driven.length = 0;
	});

	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('is driven onto a canvas where the browser can decode frames, and can be paused', async () => {
		vi.stubGlobal('ImageDecoder', class {});
		render('asset-9', { mediaType: 'gif' });
		await vi.waitFor(() => expect(driven).toHaveLength(1));

		expect(host.querySelector('canvas.picture')).not.toBeNull();
		expect(host.querySelector('img')).toBeNull();
		// The bar's own Play, with the bar's own words: one shape on every bar, whatever is above it.
		const pause = host.querySelector('[aria-label="Pause"]') as HTMLButtonElement;
		expect(pause).not.toBeNull();
		expect(pause.disabled).toBe(false);

		pause.click();
		flushSync();
		expect(driven[0].paused).toBe(1);
		expect(host.querySelector('[aria-label="Play"]')).not.toBeNull();

		(host.querySelector('[aria-label="Play"]') as HTMLButtonElement).click();
		flushSync();
		expect(driven[0].played).toBeGreaterThanOrEqual(1);
	});

	it('is paused by the same key that pauses a clip', async () => {
		vi.stubGlobal('ImageDecoder', class {});
		render('asset-9', { mediaType: 'gif' });
		await vi.waitFor(() => expect(driven).toHaveLength(1));

		const press = new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true });
		window.dispatchEvent(press);
		flushSync();

		expect(press.defaultPrevented).toBe(true);
		expect(driven[0].paused).toBe(1);
	});

	it('stays an ordinary picture where frames cannot be decoded, with no Play at all', () => {
		/*
		 * Firefox, or a page over plain http: the decoder is not there, and the `<img>` plays on.
		 * There is nothing a press would ever start, so the Play button is not drawn (rather than
		 * dimmed) and the step arrows close up.
		 */
		render('asset-9', { mediaType: 'gif' });

		expect(host.querySelector('img')).not.toBeNull();
		expect(host.querySelector('canvas.picture')).toBeNull();
		expect(host.querySelector('[aria-label="Play"]')).toBeNull();
		expect(driven).toHaveLength(0);
	});

	it('is not offered for a photograph, which has nothing to pause', () => {
		vi.stubGlobal('ImageDecoder', class {});
		render('asset-9', { mediaType: 'image' });

		expect(host.querySelector('[aria-label="Play"]')).toBeNull();
		expect(driven).toHaveLength(0);
	});
});

describe('filling the screen with it from the keyboard', () => {
	/*
	 * F fills the screen for a picture in the popout too: one shortcut, declared once for "what you
	 * are watching", and a picture the player does not draw is one of those things.
	 */

	it('answers F, through the same call the button makes', () => {
		render('asset-4');
		const { request } = fullscreenSpies();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', bubbles: true }));
		flushSync();

		expect(request).toHaveBeenCalled();
	});

	it('gives the screen back on a second press', () => {
		// The shortcut is one key for both halves (fill, and give it back) so a picture that only
		// answered the first would leave somebody in a filled screen the key cannot leave.
		render('asset-4');
		const { exit } = fullscreenSpies();
		Object.defineProperty(document, 'fullscreenElement', {
			configurable: true,
			value: host.querySelector('.stage')
		});
		document.dispatchEvent(new Event('fullscreenchange'));
		flushSync();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', bubbles: true }));
		flushSync();

		expect(exit).toHaveBeenCalled();
	});

	it('does not take the key while it IS the panel in the corner', () => {
		/* The panel is a few hundred pixels in the corner and has no screen of its own to fill, and,
		   the half that matters, the press is not swallowed: a key that is taken and then does
		   nothing never reaches whatever would have used it. */
		render('asset-1', { compact: true });
		const { request } = fullscreenSpies();

		const press = new KeyboardEvent('keydown', { key: 'f', bubbles: true, cancelable: true });
		window.dispatchEvent(press);
		flushSync();

		expect(request).not.toHaveBeenCalled();
		expect(press.defaultPrevented).toBe(false);
	});
});

describe('the drawer, in the same seven places as a clip', () => {
	/* Stepping from a clip to a picture must move nothing in the drawer under the hand, so every
	   control is drawn in the clip's order, and
	   what a picture cannot do is dimmed with the reason as its label, as a Theater cell's is.
	   Shuffle and what happens at the end stand on the bar. */
	it('draws the clip drawer, dimming what a picture cannot do, with the reason', () => {
		render('asset-1', { mediaType: 'image' });
		(host.querySelector('[aria-label="More controls"]') as HTMLElement).click();
		flushSync();
		const buttons = [...(host.querySelector('.panel') as HTMLElement).querySelectorAll('button')];
		const named = (one: Element) => one.getAttribute('aria-label') ?? '';

		expect(buttons.map(named)).toEqual([
			'A picture has no seconds to clip',
			'Screenshot',
			'A picture has one size',
			'Nothing here can open a random file',
			'A picture has no stretch to loop',
			'A picture has no stretch to save',
			'Stats for nerds'
		]);
		const dimmed = buttons.filter((one) => (one as HTMLButtonElement).disabled).map(named);
		expect(dimmed).toHaveLength(5);
		expect(dimmed).not.toContain('Screenshot');
		/* What happens at the end is the run's, and it moves the run off a picture: live, on the bar. */
		const end = host.querySelector('.middle [aria-label="Play through"]') as HTMLButtonElement;
		expect(end?.disabled).toBe(false);
		expect(host.querySelector('.middle [aria-label="Shuffle"]')).not.toBeNull();
	});
});

describe('a picture this browser cannot draw', () => {
	/* A phone's HEIC is a picture only Safari draws. Everywhere else its bytes arrive, the element
	   fails, and a view reading that alone says "Sift can't reach this file" about a file that is there.
	   The element's `error` says the same for a missing file and an undrawable one, so the server
	   is asked which it is, and each of the three answers has its own screen. */

	/** The server's answer to the HEAD the view asks after a failed draw, and what was asked. */
	function serverSays(status: number): string[] {
		const asked: string[] = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
			asked.push(`${init?.method ?? 'GET'} ${String(input)}`);
			return new Response(null, { status });
		});
		return asked;
	}

	/** The picture element fails to draw what it was given, and every answer after it lands. */
	async function failsToDraw(): Promise<void> {
		host.querySelector('img')?.dispatchEvent(new Event('error'));
		await vi.waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
		await Promise.resolve();
		flushSync();
	}

	it('is drawn from the copy Sift made of it, when the file is there', async () => {
		const asked = serverSays(200);
		render('asset-7');

		await failsToDraw();

		expect(asked).toEqual(['HEAD /api/assets/asset-7/stream']);
		expect(host.querySelector('img')?.getAttribute('src')).toBe('/api/assets/asset-7/rendition');
		expect(host.textContent).not.toContain("Sift can't reach this file");
	});

	it('says the browser cannot show it when there is no copy either, never that it is gone', async () => {
		serverSays(200);
		render('asset-7');
		await failsToDraw();

		host.querySelector('img')?.dispatchEvent(new Event('error'));
		flushSync();

		expect(host.textContent).toContain("This browser can't show this kind of picture");
		expect(host.textContent).toContain('Save to device gives you the original');
		expect(host.textContent).not.toContain("Sift can't reach this file");
		expect(host.querySelector('img')).toBeNull();
	});

	describe('that the file says only some browsers draw', () => {
		afterEach(() => {
			vi.unstubAllGlobals();
			forgetHeic();
		});

		/** This browser's answer to "do you draw HEIC", or no way to ask at all (no decoder to ask
		 *  and no sample drawn). */
		function browserSays(draws: boolean | undefined): void {
			forgetHeic();
			vi.stubGlobal(
				'ImageDecoder',
				draws === undefined ? undefined : { isTypeSupported: async () => draws }
			);
			if (draws === undefined) vi.stubGlobal('Image', undefined);
		}

		async function settled(): Promise<void> {
			await vi.waitFor(() => {
				flushSync();
				if (!host.querySelector('img')?.getAttribute('src')) throw new Error('still holding');
			});
		}

		it('is drawn from the copy immediately where the browser says it cannot draw it', async () => {
			const asked = serverSays(200);
			browserSays(false);
			render('asset-7', { mayNotDraw: true });
			await settled();

			expect(host.querySelector('img')?.getAttribute('src')).toBe('/api/assets/asset-7/rendition');
			expect(asked.filter((one) => one.includes('/stream'))).toEqual([]);
		});

		it('is drawn from the original where the browser says it can', async () => {
			serverSays(200);
			browserSays(true);
			render('asset-7', { mayNotDraw: true });
			await settled();

			expect(host.querySelector('img')?.getAttribute('src')).toBe('/api/assets/asset-7/stream');
		});

		it('goes to the copy after one refusal, asking nothing more, where the browser cannot say', async () => {
			const asked = serverSays(200);
			browserSays(undefined);
			render('asset-7', { mayNotDraw: true });
			await settled();
			expect(host.querySelector('img')?.getAttribute('src')).toBe('/api/assets/asset-7/stream');

			host.querySelector('img')?.dispatchEvent(new Event('error'));
			flushSync();

			expect(host.querySelector('img')?.getAttribute('src')).toBe('/api/assets/asset-7/rendition');
			expect(asked.filter((one) => one.includes('/stream'))).toEqual([]);
		});

		it('says the browser cannot show it when the copy it went to first is missing', async () => {
			serverSays(200);
			browserSays(false);
			render('asset-7', { mayNotDraw: true });
			await settled();

			await failsToDraw();

			expect(host.textContent).toContain("This browser can't show this kind of picture");
			expect(host.textContent).not.toContain("Sift can't reach this file");
		});
	});

	it('is still a file Sift cannot reach when the server has nothing there', async () => {
		serverSays(404);
		render('asset-7');

		await failsToDraw();

		expect(host.textContent).toContain("Sift can't reach this file");
		expect(host.textContent).not.toContain("This browser can't show this kind of picture");
	});
});
