import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { pageScroll, scrollingBody } from './page-scroll';

/* Putting a screen back where it was left.
 *
 * Listening for `scroll` events cannot tell a person scrolling from a screen being torn down, so
 * a wall left far down would come back near the top. This module is asked by the router at two exact moments and at no other, so there is no traffic to tell apart;
 * what these tests pin is that it survives the two things that happen around those moments. The
 * returning screen has NOTHING IN IT when the position arrives, and the media wall sets its own box
 * back to nought once its first page lands.
 */

/** A box with a size, because jsdom lays nothing out and every one of these reads as nought. */
function box(parent: ParentNode, { view = 800, content = 3000, top = 0 } = {}) {
	const element = document.createElement('div');
	parent.appendChild(element);
	let scrollTop = top;
	let scrollHeight = content;
	Object.defineProperty(element, 'scrollTop', {
		get: () => scrollTop,
		set: (value: number) => {
			scrollTop = value;
		},
		configurable: true
	});
	Object.defineProperty(element, 'clientHeight', { get: () => view, configurable: true });
	Object.defineProperty(element, 'scrollHeight', {
		get: () => scrollHeight,
		configurable: true
	});
	return {
		element,
		/** What a screen looks like before its first answer arrives: a box with nothing in it. */
		empty: () => {
			scrollHeight = view;
		},
		fill: () => {
			scrollHeight = content;
		},
		/** Part of the answer, for a screen that is still filling. */
		grow: (height: number) => {
			scrollHeight = height;
		}
	};
}

let frames: FrameRequestCallback[] = [];
let clock = 0;

/** Run whatever asked for the next frame, once. */
function frame(ms = 16) {
	clock += ms;
	const due = frames;
	frames = [];
	for (const callback of due) callback(clock);
}

beforeEach(() => {
	frames = [];
	clock = 0;
	vi.spyOn(performance, 'now').mockImplementation(() => clock);
	vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
		frames.push(callback);
		return frames.length;
	});
	vi.stubGlobal('cancelAnimationFrame', (handle: number) => {
		frames[handle - 1] = () => {};
	});
});

afterEach(() => {
	pageScroll.restore(0); // ends anything still holding, so one test cannot reach into the next
	document.body.replaceChildren();
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

describe('what the router is told', () => {
	it('answers nought when no screen has said it has a scrolling box', () => {
		expect(pageScroll.capture()).toBe(0);
	});

	it('answers where the screen is scrolled to, in whole pixels', () => {
		const wall = box(document.body, { top: 406.4 });
		scrollingBody(wall.element);

		expect(pageScroll.capture()).toBe(406);
	});

	it('answers the INNERMOST box when a screen draws a frame inside a frame', () => {
		/* `/organize/[queue]` does exactly this: the page draws one and the panel it chooses draws
		   another. The inner one is what a person scrolled; the outer one is holding it still. */
		const outer = box(document.body, { top: 0 });
		const inner = box(outer.element, { top: 250 });
		scrollingBody(outer.element);
		scrollingBody(inner.element);

		expect(pageScroll.capture()).toBe(250);
	});

	it('does not answer with a box that is no longer on the page', () => {
		const gone = box(document.body, { top: 300 });
		scrollingBody(gone.element);
		gone.element.remove();

		expect(pageScroll.capture()).toBe(0);
	});

	it('forgets a box when its screen tears down', () => {
		const wall = box(document.body, { top: 300 });
		const forget = scrollingBody(wall.element);
		forget();

		expect(pageScroll.capture()).toBe(0);
	});
});

describe('putting a position back', () => {
	it('waits for the screen to have something in it, then puts it back', () => {
		const wall = box(document.body, { top: 0 });
		wall.empty();
		scrollingBody(wall.element);

		pageScroll.restore(406);
		frame();
		expect(wall.element.scrollTop).toBe(0); // nothing to scroll yet

		wall.fill();
		frame();
		expect(wall.element.scrollTop).toBe(406);
	});

	it('holds it against a screen that sets its own box back to the top', () => {
		/* The media wall does this on purpose (a page you have just turned to belongs at the top)
		   and it does it AFTER its first answer lands, which is after the position arrives. */
		const wall = box(document.body, { top: 0 });
		scrollingBody(wall.element);

		pageScroll.restore(406);
		frame();
		expect(wall.element.scrollTop).toBe(406);

		wall.element.scrollTop = 0;
		frame();
		expect(wall.element.scrollTop).toBe(406);
	});

	it('keeps waiting while the screen is still filling, however slow the answer is', () => {
		/*
		 * A busy machine: a wall left at the bottom must come back all the way there, even while
		 * the rows under it are still arriving. The clock is for a screen whose content is not
		 * coming, so anything arriving restarts it.
		 */
		const wall = box(document.body, { top: 0 });
		wall.empty();
		scrollingBody(wall.element);

		pageScroll.restore(406);
		frame(1000);
		wall.grow(1000); // 200 of range: taller, and still not enough
		frame(1000);
		wall.grow(1100);
		frame(1000); // three seconds in, which a fixed deadline would have given up on twice over

		wall.fill();
		frame();
		expect(wall.element.scrollTop).toBe(406);
	});

	it('gives up when a screen stops growing without ever being tall enough', () => {
		/* The other half of the rule above, and the one that stops a hold living for ever: a screen
		   that will never have the room for the position it is being asked about. */
		const wall = box(document.body, { top: 0 });
		wall.empty();
		scrollingBody(wall.element);

		pageScroll.restore(406);
		frame(100);
		frame(1600); // a second and a half with nothing arriving

		wall.fill();
		frame();
		expect(wall.element.scrollTop).toBe(0);
		expect(frames).toHaveLength(0);
	});

	it('gives up rather than jumping a screen a second and a half later', () => {
		const wall = box(document.body, { top: 0 });
		scrollingBody(wall.element);

		pageScroll.restore(406);
		frame(2000);
		wall.element.scrollTop = 0;
		frame();

		expect(wall.element.scrollTop).toBe(0);
		expect(frames).toHaveLength(0);
	});

	it('stops the moment somebody scrolls for themselves', () => {
		const wall = box(document.body, { top: 0 });
		scrollingBody(wall.element);

		pageScroll.restore(406);
		frame();
		window.dispatchEvent(new Event('wheel'));
		wall.element.scrollTop = 0;
		frame();

		expect(wall.element.scrollTop).toBe(0);
	});

	it.each(['keydown', 'pointerdown', 'touchstart'])('stops on a %s too', (gesture) => {
		const wall = box(document.body, { top: 0 });
		scrollingBody(wall.element);

		pageScroll.restore(406);
		frame();
		window.dispatchEvent(new Event(gesture));
		wall.element.scrollTop = 0;
		frame();

		expect(wall.element.scrollTop).toBe(0);
	});

	it('does nothing at all for a screen that was at the top', () => {
		const wall = box(document.body, { top: 0 });
		scrollingBody(wall.element);

		pageScroll.restore(0);

		expect(frames).toHaveLength(0);
	});

	it.each([undefined, null, 'up a bit', Number.NaN, -20])(
		'refuses %s, which is what a snapshot from an older build looks like',
		(kept) => {
			const wall = box(document.body, { top: 0 });
			scrollingBody(wall.element);

			pageScroll.restore(kept);

			expect(frames).toHaveLength(0);
			expect(wall.element.scrollTop).toBe(0);
		}
	);

	it('reports the position it is still putting back, not the nought it is sitting at', () => {
		/* A wall rewrites its own address as you turn pages, and that is a navigation, so a capture
		   can land in the middle of a restore. Answering with the live box would record the top of
		   the screen as the place this entry was left. */
		const wall = box(document.body, { top: 0 });
		wall.empty();
		scrollingBody(wall.element);

		pageScroll.restore(406);
		frame();

		expect(pageScroll.capture()).toBe(406);
	});

	it('starts the new position rather than racing the old one', () => {
		const wall = box(document.body, { top: 0 });
		scrollingBody(wall.element);

		pageScroll.restore(406);
		frame();
		pageScroll.restore(120);
		frame();

		expect(wall.element.scrollTop).toBe(120);
		expect(frames).toHaveLength(1);
	});
});
