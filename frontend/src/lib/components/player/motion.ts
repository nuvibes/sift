// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * A player's screen changing: ONE movement for every player, on one pair of tokens.
 *
 * Every player changes the size of what it shows: the Player fills the screen and comes back, a
 * file goes down to the mini player and back up to full size, the mini player goes down to the
 * Audio player, the viewer opens and closes, a Theater wall fills the screen. Left to themselves
 * each would move its own way or not at all: a picture snapping into full screen, a corner panel
 * simply there, a filled wall fading at a pace of its own. So they share this: the box the
 * picture stood in eases into the box it stands in now, on `--dur-stage` and `--ease-stage`,
 * and a change that makes it SMALLER takes `--dur-stage-leave` on `--ease-stage-leave`, one pace
 * quicker, because leaving is not arriving backwards.
 *
 * ## Why the box eases rather than the change
 *
 * The browser's own full screen cannot be animated: the element is in the top layer, the size of
 * the screen, from one frame to the next. What can be animated is where the picture appears to
 * be: at the first frame after the change it is drawn at the size and the place of the box it
 * left (`transform`, which is composited and moves nothing around it) and it grows or shrinks into
 * its new box. The same arithmetic answers a panel changing shape, so a class change (the corner
 * panel to the Audio player) is the same movement as a full screen.
 *
 * ## Where the box it left comes from
 *
 * It has to be measured BEFORE the change, and by the time anything is told about a change it
 * has happened. So a stage that moves is held here with where it last stood, taken again on
 * every press and key (the moment before anything a press can do) and on every resize, and by
 * whatever asks for a change in code (`aboutToChange`, which `fullscreen.ts` calls).
 *
 * Reduced motion makes every one of these instant: nothing travels and nothing fades.
 */

import { bezier, durationToken, easingToken, motion } from '$lib/shell/motion.svelte';

/** Every stage that moves, and the box it last stood in. */
const stood = new Map<HTMLElement, DOMRect>();

/** The tokens this reads, named once so a gate and a test can ask for exactly these. */
const STAGE_TOKENS = {
	arrive: '--dur-stage',
	leave: '--dur-stage-leave',
	ease: '--ease-stage',
	easeLeave: '--ease-stage-leave'
} as const;

/* What a document without the stylesheet (a test, a server render) falls back to: the values the
   tokens stand for, `--dur-slow` and `--dur-base` on the entrance and exit curves. */
const FALLBACK = {
	arrive: 320,
	leave: 200,
	ease: [0.2, 0, 0, 1],
	easeLeave: [0.4, 0, 1, 1]
};

/** How long a screen change takes and the curve it takes it on. Zero under reduced motion. */
export function stagePace(leaving: boolean): { duration: number; curve: number[] } {
	const duration = leaving
		? durationToken(STAGE_TOKENS.leave, FALLBACK.leave)
		: durationToken(STAGE_TOKENS.arrive, FALLBACK.arrive);
	const curve = leaving
		? easingToken(STAGE_TOKENS.easeLeave, FALLBACK.easeLeave)
		: easingToken(STAGE_TOKENS.ease, FALLBACK.ease);
	return { duration: motion.reduced ? 0 : duration, curve };
}

/**
 * The transform that draws a box at `to` as though it were still at `from`.
 *
 * One scale for both axes, so a picture is never squashed on the way: a 16:9 box and a 16:10
 * screen are nearly the same shape, and a panel and a strip are not, where the fade carries the
 * difference. Measured about the centre, which is where a box's `transform-origin` sits.
 */
export function travel(from: DOMRect, to: DOMRect): { dx: number; dy: number; scale: number } {
	return {
		dx: from.left + from.width / 2 - (to.left + to.width / 2),
		dy: from.top + from.height / 2 - (to.top + to.height / 2),
		scale: to.width > 0 ? from.width / to.width : 1
	};
}

/*
 * The individual `translate` and `scale` properties rather than `transform`, and that is the whole
 * of what lets the browser's own full screen move at all: its stylesheet pins `transform: none
 * !important` on the element filling the screen, and an important declaration beats an
 * animation. The two properties are not pinned. A box that already stands on a `translate` of its
 * own (the Audio player is centred by one) keeps it: the offset is added to it, and the end of the
 * movement is left to the stylesheet (an implicit last keyframe), so it lands exactly where it
 * stands.
 */
function offsetFrom(node: HTMLElement, dx: number, dy: number): string {
	const own = getComputedStyle(node).translate;
	const [x = '0px', y = '0px'] = own && own !== 'none' ? own.split(' ') : [];
	return `calc(${x} + ${dx}px) calc(${y} + ${dy}px)`;
}

/**
 * Ease a box from where it stood into where it stands now. Returns the animation, or null where
 * there is nothing to move (no box to come from, the same box, reduced motion).
 */
export function settle(
	node: HTMLElement,
	from: DOMRect | null,
	options: { fade?: boolean } = {}
): Animation | null {
	if (from === null || typeof node.animate !== 'function') return null;
	const to = node.getBoundingClientRect();
	if (stood.has(node)) stood.set(node, to);
	if (!(to.width > 0 && from.width > 0)) return null;
	const { dx, dy, scale } = travel(from, to);
	if (Math.abs(dx) < 1 && Math.abs(dy) < 1 && Math.abs(scale - 1) < 0.01) return null;
	const { duration, curve } = stagePace(to.width < from.width);
	if (duration <= 0) return null;
	/* `offset: 0` is load-bearing: a lone keyframe without one is taken as the END of the movement,
	   which plays the whole thing backwards and then snaps. */
	const start: Keyframe = { offset: 0, translate: offsetFrom(node, dx, dy), scale: `${scale}` };
	if (options.fade) start.opacity = 0;
	return node.animate([start], {
		duration,
		easing: `cubic-bezier(${curve.join(', ')})`
	});
}

/* --- Where each stage last stood ------------------------------------------------------------ */

function measure(node: HTMLElement): void {
	// Not while it is moving: the box mid-flight is not where it stands.
	if (typeof node.getAnimations === 'function' && node.getAnimations().length > 0) return;
	stood.set(node, node.getBoundingClientRect());
}

/** Take where every stage stands now, just before code asks for a change. */
export function aboutToChange(): void {
	for (const node of stood.keys()) measure(node);
}

/*
 * The one listener set, shared by every stage on the page: a press or a key is the moment before
 * anything it does, and the document hears it first (capture). Added with the first stage and
 * taken away with the last.
 */
let listening = 0;

function heard(): void {
	aboutToChange();
}

function filled(): void {
	/* The browser has already moved the element; the page's own answer to it (a class the screen
	   sets when it hears the same event) lands in a microtask. A frame later both have, and the
	   first frame drawn is the one the movement starts from. */
	const moved = [...stood.entries()];
	/* The outermost only. A Theater wall is a stage holding a stage per cell, and every one of them
	   changed size: the wall carries its cells with it, and a cell easing inside a wall that is
	   easing too would be two movements, compounded, for one change. */
	const outer = moved.filter(
		([node]) => !moved.some(([other]) => other !== node && other.contains(node))
	);
	requestAnimationFrame(() => {
		for (const [node, from] of outer) settle(node, from);
	});
}

function listen(): void {
	listening += 1;
	if (listening > 1) return;
	document.addEventListener('pointerdown', heard, { capture: true, passive: true });
	document.addEventListener('keydown', heard, { capture: true, passive: true });
	document.addEventListener('fullscreenchange', filled);
}

function stopListening(): void {
	listening -= 1;
	if (listening > 0) return;
	document.removeEventListener('pointerdown', heard, { capture: true });
	document.removeEventListener('keydown', heard, { capture: true });
	document.removeEventListener('fullscreenchange', filled);
}

/**
 * A player's stage: eases into its new box whenever the screen fills or lets go, and whenever
 * `key` changes (a class that moves it, such as the corner panel becoming the Audio player).
 * `fade` for a change of shape, where the picture is not the whole of what moves.
 */
export function screenChanges(
	node: HTMLElement,
	options: { key?: unknown; fade?: boolean } = {}
): { update: (next: { key?: unknown; fade?: boolean }) => void; destroy: () => void } {
	let current = options;
	stood.set(node, node.getBoundingClientRect());
	listen();
	const watcher =
		typeof ResizeObserver === 'function' ? new ResizeObserver(() => measure(node)) : null;
	watcher?.observe(node);
	return {
		update(next) {
			const changed = next.key !== current.key;
			current = next;
			if (!changed) return;
			settle(node, stood.get(node) ?? null, { fade: next.fade });
		},
		destroy() {
			watcher?.disconnect();
			stood.delete(node);
			stopListening();
		}
	};
}

/* --- A picture handed from one player to another --------------------------------------------- */

let handed: DOMRect | null = null;

/** Say where the picture stands as it is handed to another player (docking, going back up). */
export function handPlace(box: DOMRect | null): void {
	handed = box;
}

/** Where the picture arriving here was handed from, once. Answering clears it. */
export function takePlace(): DOMRect | null {
	const box = handed;
	handed = null;
	return box;
}

/** What the framework needs to run a transition. See `$lib/shell/motion.svelte`. */
interface Transition {
	duration: number;
	easing: (t: number) => number;
	css: (t: number, u: number) => string;
}

/** How small a player grows from when nothing says where it came from. */
const GROW_FROM = 0.88;

/**
 * A player arriving and leaving: the viewer opening over the page, the corner panel appearing.
 *
 * Arriving, it grows out of the box it was handed from (`from`), or from a little smaller than
 * itself where it stands; leaving, it fades one pace quicker on the exit curve, because the
 * player it went to is arriving at the same moment and one movement is enough. Reduced motion is
 * instant both ways.
 */
export function stageTransition(
	node: Element,
	options: { from?: () => DOMRect | null } = {}
): (how?: { direction?: 'in' | 'out' | 'both' }) => Transition {
	return (how) => {
		const leaving = how?.direction === 'out';
		const { duration, curve } = stagePace(leaving);
		if (leaving) return { duration, easing: bezier(curve), css: (t) => `opacity: ${t}` };
		const box = node.getBoundingClientRect();
		const place = options.from?.() ?? null;
		const usable = place !== null && place.width > 0 && box.width > 0;
		const { dx, dy, scale } = usable ? travel(place, box) : { dx: 0, dy: 0, scale: GROW_FROM };
		return {
			duration,
			easing: bezier(curve),
			css: (t, u) =>
				`opacity: ${t}; transform: translate(${u * dx}px, ${u * dy}px) scale(${1 - u * (1 - scale)})`
		};
	};
}
