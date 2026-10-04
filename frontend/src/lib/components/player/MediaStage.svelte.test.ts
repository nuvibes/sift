/*
 * The frame every asset is shown in, and the three things it makes true: the dialog does not resize
 * itself around each file, stepping onto a photograph stays in fullscreen, and a photograph has a
 * way onward, since next and previous belong to the stage rather than the video's controls.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount } from 'svelte';
import MediaStage from './MediaStage.svelte';
// The component's own text, for the one check below that has to read the stylesheet rather than a
// rendered element. `?raw` is the bundler's way of asking for a file as a string.
import stageSource from './MediaStage.svelte?raw';
import StageHarness from './StageHarness.svelte';

let host: HTMLElement;

afterEach(() => {
	host?.remove();
});

/** Something to put on the stage. What it is does not matter; that it is the same box does. */
const picture = createRawSnippet(() => ({
	render: () => '<img alt="" src="/api/assets/a/stream" />'
}));

function render(props: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);
	mount(MediaStage, { target: host, props: { media: picture, ...props } });
	// `mount` returns before the mount effects have run, so the element the stage binds to itself is
	// still null until this. In a browser the two are never observed apart; in a test they are.
	flushSync();
	return host.querySelector('.stage') as HTMLElement;
}

describe('the frame itself', () => {
	it('is one element, whatever is put inside it', () => {
		// The property everything else here rests on. The browser draws the fullscreened ELEMENT, so
		// the stage being the same element for a video and for a photograph is what lets somebody
		// step from one to the other without being thrown out of fullscreen.
		const stage = render();

		expect(stage).not.toBeNull();
		expect(stage.querySelector('img')).not.toBeNull();
	});

	it('is a fixed shape rather than one the picture decides', () => {
		/* A modal sized to its contents is a different size for a portrait clip, a landscape one and
		 * a screenshot, so clicking through a grid would make the dialog jump about under the pointer. */
		const stage = render();

		expect(stage.style.length === 0 || !stage.style.height).toBe(true);
		// The height comes from the stylesheet rather than from the media, which is what makes it
		// the same for every asset. Asserted as "the element does not size itself", because jsdom
		// applies no stylesheet and a computed pixel height here would be a test of nothing.
		expect(stage.className).toContain('stage');
	});
});

/*
 * The way through the list is not the stage's: the outer pair of every bar (the player's, the still
 * viewer's and a theater cell's) is the same previous/next, so a photograph has its way onward on
 * the same row as a clip, and the stage does not know what is inside it. `PlayerBar` keeps focus
 * off a button pressed with the mouse.
 */
describe('fullscreen, which is the whole reason the frame is shared', () => {
	/* Mounted through a harness rather than with a snippet, because what is under test is the handle
	 * the stage puts in context, and a snippet written here is compiled here, so it would read no
	 * context however deep inside the stage it is drawn. */
	function withProbe(): HTMLElement {
		host = document.createElement('div');
		document.body.append(host);
		mount(StageHarness, { target: host });
		flushSync();
		return host.querySelector('.stage') as HTMLElement;
	}

	it('hands the media a way to reach the frame', () => {
		const stage = withProbe();

		expect(stage.querySelector('.probe-has-frame')?.textContent).toBe('yes');
	});

	it('asks the browser for the STAGE, and nothing narrower', () => {
		/* The whole arrangement in one assertion. Requesting the video instead would mean the
		 * element being drawn is not the one whose contents get swapped, so stepping to the next
		 * asset would replace what the browser is holding and fullscreen would end, which is the bug
		 * this shape exists to fix. */
		const stage = withProbe();
		const request = vi.fn();
		stage.requestFullscreen = request;

		(stage.querySelector('.probe-fullscreen') as HTMLElement).click();
		flushSync();

		expect(request).toHaveBeenCalledOnce();
	});

	it('and asks to leave rather than to enter when it is already there', () => {
		// Pressing it while fullscreen must leave fullscreen: asking the browser for something
		// already true does nothing and reports nothing.
		const stage = withProbe();
		const exit = vi.fn();
		document.exitFullscreen = exit;
		Object.defineProperty(document, 'fullscreenElement', { value: stage, configurable: true });

		(stage.querySelector('.probe-fullscreen') as HTMLElement).click();
		flushSync();

		expect(exit).toHaveBeenCalledOnce();
		Object.defineProperty(document, 'fullscreenElement', { value: null, configurable: true });
	});

	it('fills the window with a picture where the browser lets nothing fill the screen, and leaves on the next press', () => {
		/* An iPhone: no element may take the screen, and a picture has no video to hand over. The
		   press must still do something there. */
		Object.defineProperty(document, 'fullscreenEnabled', { value: false, configurable: true });
		const stage = withProbe();
		const press = () => {
			(stage.querySelector('.probe-fullscreen') as HTMLElement).click();
			flushSync();
		};

		press();
		expect(stage.classList.contains('filling')).toBe(true);
		expect(stage.querySelector('.probe-fullscreen')?.textContent?.trim()).toBe('leave');

		press();
		expect(stage.classList.contains('filling')).toBe(false);
		expect(stage.querySelector('.probe-fullscreen')?.textContent?.trim()).toBe('enter');
		Object.defineProperty(document, 'fullscreenEnabled', { value: undefined, configurable: true });
	});
});

describe('the frame and its bar go square together', () => {
	/*
	 * The frame is rounded and its bar rounds its own bottom corners to match, because a
	 * backdrop-filter escapes an ancestor's rounded clip. Fullscreen takes both square, and the
	 * rules for the frame and for the bar must name the same states (the browser's `:fullscreen` as
	 * well as this component's class), or a line of the frame's ground shows under the timeline.
	 *
	 * Read out of the source, deliberately: there is no real fullscreen in jsdom, so a rendered
	 * test asserting the class path would pass with the pseudo-class missing. What can be checked
	 * is that the two rules still name the same states.
	 *
	 * A path rather than `import.meta.url`: the test runs through the bundler, which rewrites that
	 * to an http URL, and `readFileSync` wants a file.
	 *
	 * Comments are taken out first: the prose above these rules explains the fullscreen
	 * pseudo-class, so a match reading the text before a brace would pick the word out of the
	 * explanation and pass with the rule missing.
	 */
	const source = stageSource.replace(/\/\*[\s\S]*?\*\//g, '');

	/** The selectors of every rule in this component that takes the bar's radius off. */
	function squaringSelectors(): string {
		const rules = source.matchAll(/([^{}]*player-bar[^{}]*)\{([^}]*)\}/g);
		return [...rules]
			.filter(([, , body]) => /border-radius:\s*0/.test(body))
			.map(([, selector]) => selector)
			.join(' ');
	}

	it('so the bar is squared by the browser-s fullscreen as well as by the class', () => {
		const selectors = squaringSelectors();

		expect(selectors, 'the bar is not squared by the browser-s own fullscreen').toContain(
			':fullscreen'
		);
		expect(selectors, 'the bar is not squared by the class').toContain('.fullscreen ');
	});

	it('and the frame is squared by both, which is what the bar has to match', () => {
		/* The frame's own rule, not one of the rules about what is drawn INSIDE a fullscreen frame.
		 * Several of those name the same pseudo-class (the bar, the progress line) and taking
		 * the first match would find whichever happened to be written highest in the file. */
		const frame = [...source.matchAll(/([^{}]*\.stage:fullscreen[^{}]*)\{([^}]*)\}/g)].find(
			([, selector]) => !selector.includes(':global(')
		);

		expect(frame?.[1], 'the frame no longer answers the browser-s fullscreen').toBeTruthy();
		expect(frame?.[1]).toContain('.stage.fullscreen');
		expect(frame?.[2]).toMatch(/border-radius:\s*0/);
	});
});

/** A notice to hang in the corner. Its contents do not matter; its timing does. */
const word = createRawSnippet(() => ({ render: () => '<span>note</span>' }));

describe('a notice over the picture', () => {
	it('is not drawn when there is nothing to say', () => {
		const stage = render();

		expect(stage.querySelector('.notice')).toBeNull();
	});

	/*
	 * A pointer moving over the stage, the way the framework will actually see one.
	 *
	 * `pointermove` is one of the events the framework listens for once at the root and hands out
	 * from there, rather than binding to each element, so a dispatched event has to bubble to
	 * reach the handler at all. A non-bubbling one is delivered to nothing, and a test that ends by
	 * asserting the notice is up then passes because the notice was up before it started and
	 * nothing ever happened.
	 */
	function movePointerOver(element: HTMLElement) {
		element.dispatchEvent(new PointerEvent('pointermove', { bubbles: true }));
		flushSync();
	}

	/** A machine with a pointer that can rest, which is the only kind whose bar fades. */
	function withHover(body: () => void) {
		vi.useFakeTimers();
		vi.stubGlobal('matchMedia', (query: string) => ({
			matches: query.includes('hover'),
			media: query
		}));
		try {
			body();
		} finally {
			vi.useRealTimers();
			vi.unstubAllGlobals();
		}
	}

	it('goes away on its own once nothing is holding it up', () => {
		withHover(() => {
			const stage = render({ notice: word });
			const notice = stage.querySelector('.notice') as HTMLElement;

			movePointerOver(stage);
			expect(notice.classList.contains('up'), 'down before the clock has run').toBe(true);

			vi.advanceTimersByTime(20_000);
			flushSync();

			expect(notice.classList.contains('up')).toBe(false);
		});
	});

	it('comes back on the same wake that brings the bar back', () => {
		withHover(() => {
			const stage = render({ notice: word });
			const notice = stage.querySelector('.notice') as HTMLElement;
			// Down first. Asserting it is up from a standing start says nothing: it starts up.
			movePointerOver(stage);
			vi.advanceTimersByTime(20_000);
			flushSync();
			expect(notice.classList.contains('up'), 'never went down to come back from').toBe(false);

			movePointerOver(stage);

			expect(notice.classList.contains('up')).toBe(true);
			expect(stage.classList.contains('resting'), 'the bar did not come back with it').toBe(false);
		});
	});

	it('rests for longer than the bar does', () => {
		/* Read from the source rather than by running the clock. The clocks only start where a
		 * pointer can go idle, and jsdom reports no hover capability at all, so a timing test here
		 * measures nothing and passes whatever the numbers are. The two constants and the fact that
		 * one wake sets both are the whole of the behaviour, and they are visible here. */
		const bar = Number(/const IDLE_MS = (\d+)/.exec(stageSource)?.[1]);
		const rest = Number(/const NOTICE_IDLE_MS = (\d+)/.exec(stageSource)?.[1]);

		expect(bar).toBeGreaterThan(0);
		expect(rest).toBeGreaterThan(bar);

		const wake = /function wake\(\)[\s\S]*?\n\t\}/.exec(stageSource)?.[0] ?? '';
		expect(wake).toContain('IDLE_MS');
		expect(wake).toContain('NOTICE_IDLE_MS');
	});

	it('does not fade out from under a pointer resting on it', () => {
		withHover(() => {
			const stage = render({ notice: word });
			const notice = stage.querySelector('.notice') as HTMLElement;

			movePointerOver(stage);
			// `pointerenter` does not bubble anywhere, so this one is bound to the element itself
			// and a plain dispatch on that element reaches it.
			notice.dispatchEvent(new Event('pointerenter'));
			flushSync();
			vi.advanceTimersByTime(20_000);
			flushSync();

			expect(notice.classList.contains('up')).toBe(true);
			// The test above proves the clock this is escaping was running.
		});
	});
});

describe('a finger lifting off the picture', () => {
	/* On a touch screen a finger leaves the picture every time it lifts. That is not somebody
	 * looking away, so the controls stay rather than fading a second after every tap. A mouse
	 * leaving still takes them. */
	function leave(stage: HTMLElement, pointerType: string) {
		stage.dispatchEvent(new PointerEvent('pointerenter', { pointerType }));
		stage.dispatchEvent(new PointerEvent('pointerleave', { pointerType }));
	}

	it('keeps the controls for a finger and lets them go for a mouse', () => {
		vi.useFakeTimers();
		try {
			const stage = render();
			leave(stage, 'touch');
			vi.advanceTimersByTime(1500);
			flushSync();
			expect(stage.classList.contains('resting')).toBe(false);

			leave(stage, 'mouse');
			vi.advanceTimersByTime(1500);
			flushSync();
			expect(stage.classList.contains('resting')).toBe(true);
		} finally {
			vi.useRealTimers();
		}
	});
});
