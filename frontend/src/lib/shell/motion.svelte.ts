/*
 * Whether the app may move, and the one way it moves. Either the system setting or Sift's own
 * switch can say no; then transforms drop to a fade and durations are capped. Script animates only
 * what CSS cannot: a change with no start value, measured before and after.
 */

import { animate } from 'motion';
/* Aliased: this module exports a `slide` of its own, a different movement. */
import { slide as revealSlide } from 'svelte/transition';

import { readStored, writeStored } from '$lib/shell/remembered.svelte';

const REDUCED_CAP_MS = 120;

class Motion {
	#system = $state(false);

	/**
	 * Sift's own switch, defaulting to ANIMATING: Windows turns reduced motion on for reasons
	 * unrelated to the person (battery saver, a remote session). "Follow Windows" is one press
	 * away.
	 */
	preference = $state<'system' | 'reduce' | 'full'>('full');

	readonly reduced = $derived(
		this.preference === 'reduce' || (this.preference === 'system' && this.#system)
	);

	constructor() {
		if (typeof window === 'undefined' || !window.matchMedia) return;
		const query = window.matchMedia('(prefers-reduced-motion: reduce)');
		this.#system = query.matches;
		query.addEventListener('change', (event) => {
			this.#system = event.matches;
			// The stylesheet is told too, or "Follow Windows" would stop following once the app
			// opened.
			reflectMotion();
		});
	}

	duration(ms: number): number {
		return this.reduced ? Math.min(ms, REDUCED_CAP_MS) : ms;
	}
}

/**
 * The RESOLVED answer, stamped on the root for the stylesheet, so Sift's switch can overrule the
 * media query.
 */
function reflectMotion(): void {
	if (typeof document === 'undefined') return;
	document.documentElement.dataset.motion = motion.reduced ? 'reduce' : 'full';
}

export const motion = new Motion();
export { REDUCED_CAP_MS };

/* --- Remembered in THIS BROWSER: motion costs a weak graphics card, a fact about a machine. */

/* Not exported: an exported storage key invites a second writer. */
const MOTION_KEY = 'sift.motion';

export type MotionPreference = 'system' | 'reduce' | 'full';

const MOTION_CHOICES: readonly MotionPreference[] = ['system', 'reduce', 'full'];

const DEFAULT_MOTION: MotionPreference = 'full';

/** Called once as the app starts; an unknown value means the default. */
export function loadMotionPreference(): void {
	const stored = readStored(MOTION_KEY);
	motion.preference = (MOTION_CHOICES as readonly string[]).includes(stored ?? '')
		? (stored as MotionPreference)
		: DEFAULT_MOTION;
	reflectMotion();
}

export function setMotionPreference(next: MotionPreference): void {
	motion.preference = next;
	writeStored(MOTION_KEY, next);
	reflectMotion();
}

/* --- The timings, read once from the stylesheet's custom properties rather than copied. */

const timings = new Map<string, number | number[]>();

function readToken(name: string): string {
	if (typeof document === 'undefined') return '';
	return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function durationToken(name: string, fallback: number): number {
	const known = timings.get(name);
	if (typeof known === 'number') return known;

	const raw = readToken(name);
	const seconds = raw.endsWith('ms') ? Number(raw.slice(0, -2)) : Number(raw.slice(0, -1)) * 1000;
	const value = Number.isFinite(seconds) && seconds > 0 ? seconds : fallback;
	timings.set(name, value);
	return value;
}

export function easingToken(name: string, fallback: number[]): number[] {
	const known = timings.get(name);
	if (Array.isArray(known)) return known;

	const found = /cubic-bezier\(([^)]+)\)/.exec(readToken(name));
	const numbers = found ? found[1].split(',').map((part) => Number(part)) : [];
	const value = numbers.length === 4 && numbers.every(Number.isFinite) ? numbers : fallback;
	timings.set(name, value);
	return value;
}

/**
 * `ambient` is for something that breathes, or a screen filling the window; anything waited on is
 * `slow` at most.
 */
type Pace = 'instant' | 'fast' | 'base' | 'slow' | 'ambient';

const PACE_FALLBACK: Record<Pace, number> = {
	instant: 90,
	fast: 140,
	base: 200,
	slow: 320,
	ambient: 600
};

function paceMs(pace: Pace): number {
	return durationToken(`--dur-${pace}`, PACE_FALLBACK[pace]);
}

/** The pace a thing LEAVES at: one step quicker than it arrived. */
const EXIT_PACE: Record<Pace, Pace> = {
	instant: 'instant',
	fast: 'instant',
	base: 'fast',
	slow: 'base',
	ambient: 'slow'
};

interface MoveOptions {
	pace?: Pace;
	spring?: boolean;
	delay?: number;
}

/* Only composited properties, so a sliding row never lays the page out again. */
interface Movement {
	opacity?: number | number[];
	x?: number | number[];
	y?: number | number[];
	scale?: number | number[];
	rotate?: number | number[];
}

/* Reduced motion drops the transforms and keeps the opacity: "it appeared" still explains. */
function withoutTransforms(movement: Movement): Movement {
	return movement.opacity === undefined ? {} : { opacity: movement.opacity };
}

/** Resolves when done; a caller about to touch the same element should wait, or the two fight. */
export async function move(
	element: Element,
	movement: Movement,
	options: MoveOptions = {}
): Promise<void> {
	const { pace = 'base', spring = false, delay = 0 } = options;
	const target = motion.reduced ? withoutTransforms(movement) : movement;

	// Nothing survived the reduction: no animation at all, not a shorter one.
	if (Object.keys(target).length === 0) return;

	const ease = spring
		? easingToken('--ease-spring', [0.34, 1.36, 0.44, 1])
		: easingToken('--ease', [0.2, 0, 0, 1]);

	await animate(element, target as Parameters<typeof animate>[1], {
		duration: motion.duration(paceMs(pace)) / 1000,
		delay: motion.duration(delay) / 1000,
		ease: ease as [number, number, number, number]
	});
}

/*
 * --- A screen's content arriving, and again whenever it becomes a different page: a page turn
 * makes no new element for a stylesheet to run an entrance on, so it is `move`.
 */

/** Small on purpose: at a screen's size a toast's rise reads as a lurch. */
const ARRIVAL_RISE = 6;

/**
 * An attachment: `key` returns what is on screen now, and `null` while an answer is in flight runs
 * nothing. ONE motion loop for the whole page, never one per tile.
 */
export function appears(key?: () => unknown) {
	return (element: Element) => {
		/* Plain variables: reactive state read by the body that writes it schedules it again. */
		let last: unknown;
		let started = false;

		$effect(() => {
			const now = key ? key() : 'once';
			if (now === null || now === undefined) return;
			// The same page as last time: something else woke this up.
			if (started && now === last) return;
			last = now;
			started = true;
			void move(element, { opacity: [0, 1], y: [ARRIVAL_RISE, 0] }, { pace: 'fast' });
		});
	};
}

/* --- Sliding a rearranged list: measure, change, measure, animate the difference. */

export function measure(elements: Iterable<Element>): Map<Element, DOMRect> {
	const seen = new Map<Element, DOMRect>();
	for (const element of elements) seen.set(element, element.getBoundingClientRect());
	return seen;
}

/** Call after the DOM has updated; an element not measured has nowhere to come from. */
export function slide(before: Map<Element, DOMRect>, options: MoveOptions = {}): void {
	if (motion.reduced) return;

	for (const [element, was] of before) {
		if (!element.isConnected) continue;
		const now = element.getBoundingClientRect();
		const dx = was.left - now.left;
		const dy = was.top - now.top;
		// Sub-pixel differences are rounding.
		if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;

		void move(element, { x: [dx, 0], y: [dy, 0] }, { pace: 'fast', ...options });
	}
}

/*
 * --- Arriving and leaving, declared on the element: by the time a script could animate a departure
 * the framework has removed it. These compile to keyframes, off the main thread.
 */

/* Solving a cubic bezier for y at x, so a transition has the stylesheet's exact curve. */
export function bezier([x1, y1, x2, y2]: number[]): (t: number) => number {
	const at = (a: number, b: number, t: number) =>
		3 * a * (1 - t) ** 2 * t + 3 * b * (1 - t) * t ** 2 + t ** 3;
	const slope = (a: number, b: number, t: number) =>
		3 * a * (1 - 4 * t + 3 * t ** 2) + 3 * b * (2 * t - 3 * t ** 2) + 3 * t ** 2;

	return (x: number) => {
		if (x <= 0) return 0;
		if (x >= 1) return 1;

		let t = x;
		for (let pass = 0; pass < 8; pass += 1) {
			const error = at(x1, x2, t) - x;
			if (Math.abs(error) < 1e-5) return at(y1, y2, t);
			const gradient = slope(x1, x2, t);
			if (Math.abs(gradient) < 1e-6) break;
			t -= error / gradient;
		}

		let low = 0;
		let high = 1;
		t = x;
		while (high - low > 1e-5) {
			if (at(x1, x2, t) < x) low = t;
			else high = t;
			t = (low + high) / 2;
		}
		return at(y1, y2, t);
	};
}

/** Written out rather than imported, so this module does not widen its dependencies for a type. */
interface Transition {
	duration: number;
	easing: (t: number) => number;
	css: (t: number, u: number) => string;
}

interface ArrivalOptions extends MoveOptions {
	y?: number;
	/** Positive from the right: a walk that goes both ways (the first-run steps). */
	x?: number;
	scale?: number;
}

interface Direction {
	direction?: 'in' | 'out' | 'both';
}

type Directed = (how?: Direction) => Transition;

/**
 * Arriving travels and fades; leaving only fades, one pace quicker (`EXIT_PACE`), so dismissing is
 * never as slow as appearing. Reduced motion keeps the fade.
 */
export function arrive(_node: Element, options: ArrivalOptions = {}): Directed {
	const { pace = 'base', spring = false, x = 0, y = 0, scale = 1 } = options;

	return (how) => {
		if (how?.direction === 'out') {
			return {
				duration: motion.duration(paceMs(EXIT_PACE[pace])),
				easing: bezier(easingToken('--ease-in', [0.4, 0, 1, 1])),
				css: (t) => `opacity: ${t}`
			};
		}

		const still = motion.reduced;
		const ease = spring
			? easingToken('--ease-spring', [0.34, 1.36, 0.44, 1])
			: easingToken('--ease', [0.2, 0, 0, 1]);

		return {
			duration: motion.duration(paceMs(pace)),
			easing: bezier(ease),
			css: (t, u) => {
				if (still || (x === 0 && y === 0 && scale === 1)) return `opacity: ${t}`;
				const across = x === 0 ? '' : ` translateX(${u * x}px)`;
				const shift = y === 0 ? '' : ` translateY(${u * y}px)`;
				const size = scale === 1 ? '' : ` scale(${1 - u * (1 - scale)})`;
				return `opacity: ${t}; transform:${across}${shift}${size}`;
			}
		};
	};
}

const SURFACE_RISE = 4;

/** A menu, popover, toast or tooltip: every small surface opens and closes the same way. */
export function surface(node: Element): Directed {
	return arrive(node, { pace: 'fast', y: SURFACE_RISE });
}

/** The Filter panel's `--space-6`, read from the stylesheet. */
function barDrop(): number {
	const known = timings.get('--space-6');
	if (typeof known === 'number') return known;
	const read = Number.parseFloat(readToken('--space-6'));
	const value = Number.isFinite(read) && read > 0 ? read : 24;
	timings.set('--space-6', value);
	return value;
}

/** Room round a dropping panel's clip for its shadow. */
const SHADOW_ROOM = 80;

/**
 * A panel hanging from the top bar, coming out from under it as the Filter panel does: a clip
 * opening downwards while the panel drops through it, each half on its own pace and curve. Nothing
 * fades unless motion is reduced.
 */
export function fromBar(_node: Element): Directed {
	return (how) => {
		const leaving = how?.direction === 'out';
		const still = motion.reduced;
		const drop = barDrop();
		const base = paceMs('base');
		const slow = paceMs('slow');
		const ease = bezier(easingToken('--ease', [0.2, 0, 0, 1]));
		const spring = bezier(easingToken('--ease-spring', [0.34, 1.36, 0.44, 1]));

		const frame = (above: number, open: number): string => {
			const foot = `calc(${(1 - open) * 100}% - ${above + open * SHADOW_ROOM}px)`;
			return (
				`transform: translateY(${-above}px); ` +
				`clip-path: inset(${above}px -${SHADOW_ROOM}px ${foot} -${SHADOW_ROOM}px)`
			);
		};

		if (still) {
			return {
				duration: motion.duration(paceMs(leaving ? 'fast' : 'base')),
				easing: ease,
				css: (t) => `opacity: ${t}`
			};
		}

		if (leaving) {
			return {
				duration: motion.duration(base),
				easing: ease,
				css: (t, u) => frame(drop * u, t)
			};
		}

		return {
			duration: motion.duration(slow),
			easing: (t) => t,
			css: (t) => frame(drop * (1 - spring(t)), ease(Math.min(1, (t * slow) / base)))
		};
	};
}

/** The veil fades and never travels, at `base`, so a dialog and its veil move together. */
export function veil(node: Element, options: MoveOptions = {}): Directed {
	return arrive(node, { pace: 'base', ...options });
}

export type Edge = 'foot' | 'end';

/**
 * A surface standing on an edge (a phone's sheet on the foot, a pushed page from the trailing
 * edge), travelling its whole size both ways; leaving is one pace quicker. Reduced motion fades.
 */
export function fromEdge(node: Element, options: { edge?: Edge; pace?: Pace } = {}): Directed {
	const { edge = 'foot', pace = 'base' } = options;
	const sign =
		edge === 'end' &&
		typeof getComputedStyle === 'function' &&
		getComputedStyle(node).direction === 'rtl'
			? -1
			: 1;
	const away = (u: number) =>
		edge === 'foot' ? `translateY(${u * 100}%)` : `translateX(${sign * u * 100}%)`;

	return (how) => {
		const leaving = how?.direction === 'out';
		const still = motion.reduced;
		return {
			duration: motion.duration(paceMs(leaving ? EXIT_PACE[pace] : pace)),
			easing: bezier(
				leaving ? easingToken('--ease-in', [0.4, 0, 1, 1]) : easingToken('--ease', [0.2, 0, 0, 1])
			),
			css: (t, u) => (still ? `opacity: ${t}` : `transform: ${away(u)}`)
		};
	};
}

/**
 * The file viewer growing out of the tile it was opened from and shrinking back into it, one scale
 * for both axes; with no tile, from a little smaller in place. At `slow`, its one use.
 */
export function fromPlace(node: Element, options: { from: () => DOMRect | null }): Directed {
	return (how) => {
		const leaving = how?.direction === 'out';
		const still = motion.reduced;
		const box = node.getBoundingClientRect();
		const place = options.from();
		const usable = place !== null && place.width > 0 && box.width > 0 && box.height > 0;
		const scale = usable ? Math.min(1, place.width / box.width) : GROW_FROM;
		const dx = usable ? place.left + place.width / 2 - (box.left + box.width / 2) : 0;
		const dy = usable ? place.top + place.height / 2 - (box.top + box.height / 2) : 0;
		return {
			duration: motion.duration(paceMs(leaving ? EXIT_PACE.slow : 'slow')),
			easing: bezier(
				leaving ? easingToken('--ease-in', [0.4, 0, 1, 1]) : easingToken('--ease', [0.2, 0, 0, 1])
			),
			css: (t, u) =>
				still
					? `opacity: ${t}`
					: `opacity: ${t}; transform: translate(${u * dx}px, ${u * dy}px) scale(${1 - u * (1 - scale)})`
		};
	};
}

const GROW_FROM = 0.88;

/**
 * A block OPENING AND CLOSING, taking the room with it: the framework's `slide` (it handles the
 * padding, margins and overflow) at this app's pace. Not `arrive`, which would leave a hole.
 */
export function reveal(node: Element, options: MoveOptions = {}): Transition {
	const { pace = 'slow' } = options;
	return revealSlide(node as HTMLElement, {
		// Zero rather than capped: what was asked for was no sliding.
		duration: motion.reduced ? 0 : motion.duration(paceMs(pace)),
		easing: bezier(easingToken('--ease', [0.2, 0, 0, 1]))
	}) as Transition;
}

/**
 * The rest of a framework-kept list closing up after an entry has gone, from where each one was.
 */
export function reflow(
	_node: Element,
	positions: { from: DOMRect; to: DOMRect },
	options: MoveOptions = {}
): Transition {
	const { pace = 'fast' } = options;
	const dx = positions.from.left - positions.to.left;
	const dy = positions.from.top - positions.to.top;

	return {
		duration: motion.reduced ? 0 : motion.duration(paceMs(pace)),
		easing: bezier(easingToken('--ease', [0.2, 0, 0, 1])),
		css: (_t, u) => `transform: translate(${u * dx}px, ${u * dy}px)`
	};
}

/* --- A figure counting up: only Insights' figures, once per answer; the test holds it to them. */

/**
 * The whole number rises, not each digit; reduced motion draws `to` immediately. Returns a stop.
 */
export function countUp(to: number, draw: (value: number) => void): () => void {
	if (motion.reduced || !(to > 0) || typeof requestAnimationFrame === 'undefined') {
		draw(to);
		return () => {};
	}

	const duration = paceMs('ambient');
	const ease = bezier(easingToken('--ease', [0.2, 0, 0, 1]));
	let started: number | null = null;
	let frame = 0;

	const step = (now: number) => {
		started ??= now;
		const done = Math.min(1, (now - started) / duration);
		draw(done >= 1 ? to : Math.round(to * ease(done)));
		if (done < 1) frame = requestAnimationFrame(step);
	};

	draw(0);
	frame = requestAnimationFrame(step);
	return () => cancelAnimationFrame(frame);
}
