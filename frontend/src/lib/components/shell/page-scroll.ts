// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Where a screen was scrolled to, put back when you step back to it.
//
// Every screen scrolls inside `PageFrame`'s body rather than the window, because a screen's header
// and pager must not move (see that file). The browser restores the window on a step back, and the
// window here never moved, so without this the returning screen's fresh box would start at nought.
//
// Not by listening for scrolling: while a screen is replaced something resets the box to a few
// pixels, and every reset arrives as an ordinary `scroll` event indistinguishable from a person's,
// so any version inferring the moment of leaving from that traffic records the wrong position. The
// router knows the moment exactly, and SvelteKit hands it over: `export const snapshot` on a route
// component is captured in `navigate()` before the component tree is swapped, with the old screen
// and its box intact, and restored only on a `popstate`, which is precisely "you stepped back to
// this entry". See `routes/+layout.svelte`, where it is exported: the root layout is on screen for
// every route, so one export covers every screen.
//
// The root layout has to be told which box: the scrolling body is several components down inside
// `{@render children()}` and a different element on every screen, so `PageFrame` says which one
// while it is mounted, and this module is the one place that knows.
//
// Two can be mounted at once (`/organize/[queue]` draws a frame and the panel inside it draws
// another), so the innermost answers: that is the box a person actually scrolled.

/**
 * How long to keep putting the position back once nothing more is arriving.
 *
 * A returning screen mounts empty and asks the server, so at the instant the router hands the
 * position over there is nothing to scroll. Rather than guess how long the answer takes, this holds
 * the position against the box until it sticks, which also survives screens that set `scrollTop` to
 * nought themselves when their first page lands (the media wall does: a page just turned to belongs
 * at the top).
 *
 * Measured from the last thing to arrive rather than from the step back: a fixed deadline is an
 * assumption about the machine, and on a busy one the rows can still be arriving when it expires,
 * leaving the box too short to hold the position. So the clock restarts every time the box grows
 * while still too short: a screen still filling keeps its promise however slow the answer, and a
 * screen whose content is not coming gives up after a second and a half with no growth. Once the
 * box is tall enough the clock is left alone.
 *
 * Anything a person does ends it sooner, and that, not the clock, keeps this from fighting somebody
 * who has started reading. See `GIVES_UP_ON`.
 */
const HOLD_FOR_MS = 1500;

/** The scrolling bodies on screen, in no particular order. See `innermost`. */
const bodies = new Set<HTMLElement>();

/**
 * Say that this element is a screen's scrolling body, and hand back how to stop saying it.
 *
 * Called by `PageFrame` from an effect, so it is undone when the frame goes: an element left in
 * here after its screen is gone would be answered with while it is not on the page at all.
 */
export function scrollingBody(element: HTMLElement): () => void {
	bodies.add(element);
	return () => {
		bodies.delete(element);
	};
}

/**
 * The box a person is actually scrolling: the deepest one still on the page.
 *
 * Depth rather than the order they arrived in, because the order effects run in is Svelte's business
 * and not something this should depend on. A frame that has been torn down but not yet unregistered,
 * or one rendered outside the document, is not on the page and cannot be the answer.
 */
function innermost(): HTMLElement | null {
	let found: HTMLElement | null = null;
	let deepest = -1;
	for (const element of bodies) {
		if (!element.isConnected) continue;
		let depth = 0;
		for (let node = element.parentElement; node; node = node.parentElement) depth += 1;
		if (depth > deepest) {
			deepest = depth;
			found = element;
		}
	}
	return found;
}

/** The frame we are holding a position against, while we are holding one. */
let holding: number | null = null;
/** Where we are putting it back to. Only meaningful while `holding` is set. */
let wanted = 0;
/** When to give up: a second and a half after the last thing arrived. See `HOLD_FOR_MS`. */
let until = 0;
/** The tallest the box has been while still too short, so "still filling" can be told from "not
    coming". Reset per restore, and below any real range so the first look counts as growth. */
let grownTo = -1;

/**
 * What counts as somebody having taken over.
 *
 * A wheel, a finger, a key or a press: all of which mean a person is doing something with this
 * screen, and none of which we should be fighting for the next second and a half. Captured, so a
 * handler that stops the event does not also stop this from hearing it, and passive because this
 * only reads.
 */
const GIVES_UP_ON = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const;
const LISTEN = { capture: true, passive: true } as const;

/**
 * What SvelteKit asks the root layout for. See the head of this file.
 *
 * `capture` answers the pending position rather than the live one while a restore is still in
 * flight: the wall rewrites its own address as you turn pages (`replaceState`, which is a
 * navigation), so a capture can land in the middle of one and would otherwise record the nought the
 * box is still sitting at as the place this entry was left.
 */
export const pageScroll = {
	/**
	 * Back to the top of the scrolling body, on the screen's own account: a page turn from the bar
	 * at the foot must show the new page from its first row. Here rather than in the pager, because
	 * this is the one module that knows which box is scrolling.
	 */
	toTop(): void {
		release();
		const body = innermost();
		if (body) body.scrollTop = 0;
	},

	capture(): number {
		if (holding !== null) return wanted;
		return Math.round(innermost()?.scrollTop ?? 0);
	},

	restore(top: unknown): void {
		release();
		if (typeof top !== 'number' || !Number.isFinite(top) || top <= 0) return;
		wanted = top;
		grownTo = -1;
		until = now() + HOLD_FOR_MS;
		for (const name of GIVES_UP_ON) window.addEventListener(name, release, LISTEN);
		holding = requestAnimationFrame(hold);
	}
};

function hold(): void {
	const body = innermost();
	if (body) {
		const range = body.scrollHeight - body.clientHeight;
		if (range >= wanted) {
			if (Math.round(body.scrollTop) !== wanted) body.scrollTop = wanted;
		} else if (range > grownTo) {
			// Still arriving, so the deadline is not about this screen yet. See `HOLD_FOR_MS`.
			grownTo = range;
			until = now() + HOLD_FOR_MS;
		}
	}
	if (now() >= until) {
		release();
		return;
	}
	holding = requestAnimationFrame(hold);
}

function release(): void {
	if (holding === null) return;
	cancelAnimationFrame(holding);
	holding = null;
	for (const name of GIVES_UP_ON) window.removeEventListener(name, release, LISTEN);
}

/* `performance.now` where there is one: it does not step backwards, and a wall clock can.
   `Date.now` is the fallback and is only ever asked for a
   difference of a second and a half. */
function now(): number {
	return typeof performance === 'object' ? performance.now() : Date.now();
}
