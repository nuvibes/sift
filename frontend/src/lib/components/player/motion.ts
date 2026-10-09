// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * ONE movement for every player's screen change, on `--dur-stage` (`-leave`, one pace quicker).
 * Full screen cannot be animated, so the picture is drawn at the box it LEFT and eased into its new
 * one, measured before the change on every press, key and resize. Reduced motion is instant.
 */

import { bezier, durationToken, easingToken, motion } from '$lib/shell/motion.svelte';

const stood = new Map<HTMLElement, DOMRect>();

const STAGE_TOKENS = {
	arrive: '--dur-stage',
	leave: '--dur-stage-leave',
	ease: '--ease-stage',
	easeLeave: '--ease-stage-leave'
} as const;

/* For a document without the stylesheet. */
const FALLBACK = {
	arrive: 320,
	leave: 200,
	ease: [0.2, 0, 0, 1],
	easeLeave: [0.4, 0, 1, 1]
};

export function stagePace(leaving: boolean): { duration: number; curve: number[] } {
	const duration = leaving
		? durationToken(STAGE_TOKENS.leave, FALLBACK.leave)
		: durationToken(STAGE_TOKENS.arrive, FALLBACK.arrive);
	const curve = leaving
		? easingToken(STAGE_TOKENS.easeLeave, FALLBACK.easeLeave)
		: easingToken(STAGE_TOKENS.ease, FALLBACK.ease);
	return { duration: motion.reduced ? 0 : duration, curve };
}

/** One scale for both axes, so a picture is never squashed. */
export function travel(from: DOMRect, to: DOMRect): { dx: number; dy: number; scale: number } {
	return {
		dx: from.left + from.width / 2 - (to.left + to.width / 2),
		dy: from.top + from.height / 2 - (to.top + to.height / 2),
		scale: to.width > 0 ? from.width / to.width : 1
	};
}

/* `translate` and `scale`, not `transform`, which the browser pins on the fullscreen element. */
function offsetFrom(node: HTMLElement, dx: number, dy: number): string {
	const own = getComputedStyle(node).translate;
	const [x = '0px', y = '0px'] = own && own !== 'none' ? own.split(' ') : [];
	return `calc(${x} + ${dx}px) calc(${y} + ${dy}px)`;
}

/** Null where there is nothing to move. */
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
	/* `offset: 0`: a lone keyframe without one is taken as the END. */
	const start: Keyframe = { offset: 0, translate: offsetFrom(node, dx, dy), scale: `${scale}` };
	if (options.fade) start.opacity = 0;
	return node.animate([start], {
		duration,
		easing: `cubic-bezier(${curve.join(', ')})`
	});
}

/* --- Where each stage last stood */

function measure(node: HTMLElement): void {
	if (typeof node.getAnimations === 'function' && node.getAnimations().length > 0) return;
	stood.set(node, node.getBoundingClientRect());
}

export function aboutToChange(): void {
	for (const node of stood.keys()) measure(node);
}

/* One listener set for every stage, in the capture phase. */
let listening = 0;

function heard(): void {
	aboutToChange();
}

function filled(): void {
	/* A frame later, the page's own answer has landed too. */
	const moved = [...stood.entries()];
	/* The outermost only: a wall carries its cells. */
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

/** `key` for a class change that moves it; `fade` for a change of shape. */
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

/* --- A picture handed from one player to another */

let handed: DOMRect | null = null;

export function handPlace(box: DOMRect | null): void {
	handed = box;
}

export function takePlace(): DOMRect | null {
	const box = handed;
	handed = null;
	return box;
}

interface Transition {
	duration: number;
	easing: (t: number) => number;
	css: (t: number, u: number) => string;
}

const GROW_FROM = 0.88;

/** Grows out of where it was handed from; leaving is quicker, a fade or a shrink. */
export function stageTransition(
	node: Element,
	options: { from?: () => DOMRect | null; leave?: () => 'shrink' | 'fade' } = {}
): (how?: { direction?: 'in' | 'out' | 'both' }) => Transition {
	return (how) => {
		const leaving = how?.direction === 'out';
		const { duration, curve } = stagePace(leaving);
		if (leaving && options.leave?.() !== 'shrink')
			return { duration, easing: bezier(curve), css: (t) => `opacity: ${t}` };
		const box = node.getBoundingClientRect();
		const place = leaving ? null : (options.from?.() ?? null);
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
