// SPDX-License-Identifier: AGPL-3.0-or-later
// Puts back a screen's scroll position on a step back. Screens scroll inside `PageFrame`'s body,
// not the window, so the browser cannot. The position goes through SvelteKit's `snapshot` (see
// `routes/+layout.svelte`), because a `scroll` event cannot tell a reset from a person.
// `PageFrame` registers its body; when two are mounted the innermost answers.

/**
 * How long to keep applying the position after the box last grew: a returning screen fills after
 * the step back, and a fixed deadline fails on a busy machine. Input ends it sooner.
 */
const HOLD_FOR_MS = 1500;

/** The scrolling bodies on screen, in no particular order. See `innermost`. */
const bodies = new Set<HTMLElement>();

/** Register a screen's scrolling body; returns the undo, run when the frame goes. */
export function scrollingBody(element: HTMLElement): () => void {
	bodies.add(element);
	return () => {
		bodies.delete(element);
	};
}

/** The deepest connected body; effect order is Svelte's business, so depth decides. */
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
let wanted = 0;
/** When to give up: a second and a half after the last thing arrived. See `HOLD_FOR_MS`. */
let until = 0;
/** Tallest the box has been while too short, so "still filling" is told from "not coming". */
let grownTo = -1;

/** Input that means a person took over; captured so a handler stopping it cannot hide it. */
const GIVES_UP_ON = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const;
const LISTEN = { capture: true, passive: true } as const;

/** SvelteKit's snapshot; mid-restore, `capture` answers the pending position, not nought. */
export const pageScroll = {
	/** Scroll to the top for a page turn; here because this module knows which box scrolls. */
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

// `performance.now` cannot step backwards; `Date.now` is the fallback.
function now(): number {
	return typeof performance === 'object' ? performance.now() : Date.now();
}
