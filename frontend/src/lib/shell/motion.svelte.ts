/* Whether the app is allowed to move, and the one way it moves when it is.
 *
 * Two ways to say no, and either one is enough: the setting the operating system already carries,
 * and Sift's own switch. The system one is an accessibility preference and honouring it is not
 * optional. Sift's own exists because reduced motion is also the escape hatch on a slow machine:
 * the animations are the first thing to cost a weak GPU, and someone should be able to turn them off
 * without changing a system-wide setting to do it.
 *
 * When motion is off, transforms collapse to a plain opacity change and durations are capped. The
 * app does not become a different app; it stops sliding.
 *
 * ## Why scripted animation exists here at all
 *
 * Most of what moves in Sift is a CSS transition, and that is the right tool: a hover, a colour, a
 * width. What CSS cannot do is animate a change it has no start value for: a row that was fourth
 * and is now first has no "from" position, because by the time the browser draws it, it is already
 * where it ended up. That needs measuring before and after and animating the difference, which is
 * script. `move` and `slide` below are that, and they are deliberately the only two: an animation
 * that a stylesheet can express belongs in the stylesheet.
 */

import { animate } from 'motion';
/* The framework's own disclosure, aliased: this module exports a `slide` of its own and the two are
   different movements. See `reveal` below, which is the only thing that uses it. */
import { slide as revealSlide } from 'svelte/transition';

import { readStored, writeStored } from '$lib/shell/remembered.svelte';

const REDUCED_CAP_MS = 120;

class Motion {
	/** The system preference. Watched, so it takes effect the moment it changes. */
	#system = $state(false);

	/**
	 * Sift's own switch, and it defaults to ANIMATING rather than to following the system.
	 *
	 * A deliberate choice, and it is worth writing down what it trades. Following
	 * the system is the more careful default: reduced motion is an accessibility preference, and a
	 * machine that asks for less movement is asking on behalf of somebody who needs it. Sift is a
	 * self-hosted library run by the person who installed it, and Windows turns that preference on
	 * for reasons that have nothing to do with the person (a battery saver, a remote session, a
	 * setting nobody remembers choosing), so an application following it would arrive silent and
	 * simply look broken.
	 *
	 * So the default animates, and BOTH other answers are one press away on the Appearance screen,
	 * including "Follow Windows" for anybody who wants the careful behaviour back.
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
			// The stylesheet is told too, or "Follow Windows" would follow it only until the app
			// opened and then stop, which is worse than not offering the option.
			reflectMotion();
		});
	}

	/** A duration in ms, capped when motion is reduced. Use for anything scripted. */
	duration(ms: number): number {
		return this.reduced ? Math.min(ms, REDUCED_CAP_MS) : ms;
	}
}

/**
 * Tell the STYLESHEET what was decided.
 *
 * Half of Sift's movement is CSS, and CSS cannot see this class. A stylesheet asking the operating
 * system itself, with `@media (prefers-reduced-motion: reduce)`, would leave Sift's own switch
 * unable to overrule it in either direction: "Full motion" would keep a rule killing every
 * transition with `!important` while the screen that offered the choice said it was animating.
 *
 * So the RESOLVED answer is stamped on the root element and the stylesheet keys on that. One
 * attribute, written whenever the answer changes, which includes the system changing its mind
 * while the app is open, because `#system` is watched.
 */
function reflectMotion(): void {
	if (typeof document === 'undefined') return;
	document.documentElement.dataset.motion = motion.reduced ? 'reduce' : 'full';
}

export const motion = new Motion();
export { REDUCED_CAP_MS };

/* --- Remembering the choice --------------------------------------------------------------
 *
 * In THIS BROWSER'S storage, and not on the server with the theme, which is the deliberate half.
 *
 * A theme is a fact about a person: it should follow them to another computer, and two people
 * sharing one Sift should not share a look. This is a fact about a MACHINE. The reason to turn
 * motion off, other than the accessibility one the system setting already covers, is that the
 * animations are the first thing to cost a weak graphics card, and a laptop being slow is no
 * reason for the same account to stop moving on a desktop with a real card in it.
 */

/* Not exported. Nothing outside this module has any business reading the key: the two functions
 * below are the whole interface, and `public-surface.test.ts` refuses an exported name with no
 * reader, which is the right answer, because an exported storage key invites a second writer. */
const MOTION_KEY = 'sift.motion';

export type MotionPreference = 'system' | 'reduce' | 'full';

const MOTION_CHOICES: readonly MotionPreference[] = ['system', 'reduce', 'full'];

/** What a fresh install does. Said once, so the class and the loader cannot drift apart. Not
 *  exported: the two functions below are this module's whole interface, and `public-surface.test.ts`
 *  refuses a name nothing outside reads. */
const DEFAULT_MOTION: MotionPreference = 'full';

/** Apply what was remembered. Called once, as the app starts. An unknown value means the default. */
export function loadMotionPreference(): void {
	const stored = readStored(MOTION_KEY);
	motion.preference = (MOTION_CHOICES as readonly string[]).includes(stored ?? '')
		? (stored as MotionPreference)
		: DEFAULT_MOTION;
	reflectMotion();
}

/** Choose, and remember it for this browser. */
export function setMotionPreference(next: MotionPreference): void {
	motion.preference = next;
	writeStored(MOTION_KEY, next);
	reflectMotion();
}

/* ---------------------------------------------------------------------------------------------
 * The timings, read from the stylesheet rather than written down twice.
 *
 * Every duration and curve in the app is already a custom property, and a script that hardcodes
 * 200ms is a second copy that will not follow when the first one changes. These read the real
 * value off the document once and keep it, because a computed style is a layout read and doing one
 * per animation on a list of rows is a measurable cost for a number that never changes.
 * ------------------------------------------------------------------------------------------- */

const timings = new Map<string, number | number[]>();

function readToken(name: string): string {
	if (typeof document === 'undefined') return '';
	return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** A duration token, in milliseconds. */
export function durationToken(name: string, fallback: number): number {
	const known = timings.get(name);
	if (typeof known === 'number') return known;

	const raw = readToken(name);
	const seconds = raw.endsWith('ms') ? Number(raw.slice(0, -2)) : Number(raw.slice(0, -1)) * 1000;
	const value = Number.isFinite(seconds) && seconds > 0 ? seconds : fallback;
	timings.set(name, value);
	return value;
}

/** An easing token, as the four numbers a cubic bezier is made of. */
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
 * How long a shell movement takes and the curve it takes it on. Named as the stylesheet names them.
 *
 * `ambient` is the stylesheet's fifth and it is the one to reach for LAST. It is for something that
 * breathes (a placeholder's shimmer, the tint bleeding off a picture) and for the single gesture
 * that changes the whole window at once, which is a screen filling it and leaving again. Anything
 * else somebody is waiting on wants `slow` at the very most: at 600ms a control reads as lag rather
 * than as motion.
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

/**
 * The pace a thing LEAVES at, given the one it arrived at: one step quicker.
 *
 * Something going away has already been seen, so its exit only has to say that it went. A small
 * surface that opens at `fast` closes at `instant`; a sheet that opens at `base` closes at `fast`.
 * Stated once here so no caller picks an exit of its own.
 */
const EXIT_PACE: Record<Pace, Pace> = {
	instant: 'instant',
	fast: 'instant',
	base: 'fast',
	slow: 'base',
	ambient: 'slow'
};

interface MoveOptions {
	/** Which of the app's durations this takes. */
	pace?: Pace;
	/** A spring for something that should feel picked up and put down, the default curve otherwise. */
	spring?: boolean;
	/** Held back by this many ms before starting. */
	delay?: number;
}

/* What a movement is, in the terms this app animates in.
 *
 * Deliberately narrow. The library underneath can animate almost anything; the four properties
 * here are the ones that cost nothing to animate (they are composited, so a row sliding does not
 * make the browser lay the page out again), and widening this is how a smooth app becomes a janky
 * one one property at a time.
 */
interface Movement {
	opacity?: number | number[];
	x?: number | number[];
	y?: number | number[];
	scale?: number | number[];
	rotate?: number | number[];
}

/* Everything except opacity is a transform, and a transform is exactly what reduced motion asks to
 * be rid of. So when motion is off the transforms are dropped and whatever opacity was asked for is
 * kept, which is the difference between "it appeared" and "it slid in", and the first one is still
 * an explanation. */
function withoutTransforms(movement: Movement): Movement {
	return movement.opacity === undefined ? {} : { opacity: movement.opacity };
}

/**
 * Animate one element, honouring the motion preference.
 *
 * Returns when the animation is done. A caller that does not care can ignore it; a caller that is
 * about to do something else to the same element should wait, or the two will fight over the same
 * transform.
 */
export async function move(
	element: Element,
	movement: Movement,
	options: MoveOptions = {}
): Promise<void> {
	const { pace = 'base', spring = false, delay = 0 } = options;
	const target = motion.reduced ? withoutTransforms(movement) : movement;

	// Nothing survived the reduction: put the element where it was going and stop. Returning early
	// with no animation at all is the point, not a shorter one.
	if (Object.keys(target).length === 0) return;

	const ease = spring
		? easingToken('--ease-spring', [0.34, 1.36, 0.44, 1])
		: easingToken('--ease', [0.2, 0, 0, 1]);

	await animate(element, target as Parameters<typeof animate>[1], {
		// The library counts in seconds; the app counts in milliseconds, like its stylesheet does.
		duration: motion.duration(paceMs(pace)) / 1000,
		delay: motion.duration(delay) / 1000,
		ease: ease as [number, number, number, number]
	});
}

/* --------------------------------------------------------------------------------------------- A
 * screen's content arriving.
 *
 * Anything appearing gets a fade and a small rise, and the largest thing on any screen, the
 * content itself, is no exception. A wall of forty pictures replacing an empty box instantly
 * tells nobody what happened: whether the page turned, whether a filter took hold, or whether the
 * same files were rearranged.
 *
 * ## Why this is script and not a stylesheet, given the rule at the top of this file
 *
 * That rule stands: an animation a stylesheet can express belongs in the stylesheet, and an
 * entrance IS one, once. What CSS cannot express is the SECOND one. A page turn does not create
 * a new element for the browser to run an entrance on; the same body stays where it is, holding
 * different files, and a stylesheet has no way to be told that is what happened. Replaying a CSS
 * animation means taking the class off, forcing a reflow and putting it back, which is a script
 * anyway, and `{#key}` would do it by destroying the subtree and rebuilding it, dropping every
 * decoded image on the wall to fade in the ones replacing them.
 *
 * So it is `move`, which already reads its duration and its curve from the tokens and already
 * knows what reduced motion means. Nothing new is declared here about how the app moves.
 * -------------------------------------------------------------------------------------------
 */

/** How far the content rises as it arrives, in px. Small on purpose: this is a whole page moving,
 *  and what reads as an explanation at a toast's size reads as a lurch at a screen's. */
const ARRIVAL_RISE = 6;

/**
 * Ease something in when it arrives, and again whenever it becomes a different thing.
 *
 * An attachment, so it is `{@attach appears(...)}` on the element itself. Pass a `key` returning
 * what is on screen right now: a page offset, a count, anything that changes when the content
 * does. Return `null` or `undefined` while an answer is still in flight and nothing runs: a fade
 * played over the page somebody is LEAVING explains nothing.
 *
 * With no key it runs once, when the element is first drawn, which is what a screen wants when its
 * content does not page.
 *
 * ONE animation for the whole page rather than one per tile. A stagger across a wall is a long
 * shimmer and real work every frame at exactly the moment the browser is decoding forty images; the
 * page arriving as one object carries the same information at a fixed cost, which is what makes it
 * affordable on every screen rather than on the few somebody remembered to decorate.
 */
export function appears(key?: () => unknown) {
	return (element: Element) => {
		/* Plain variables, not `$state`. The effect below writes and reads them, and reactive state
		   read by the same body that writes it schedules that body again: a guard that runs twice,
		   silently. */
		let last: unknown;
		let started = false;

		$effect(() => {
			const now = key ? key() : 'once';
			// Nothing has arrived: still loading, or nothing to show.
			if (now === null || now === undefined) return;
			// The same page as last time, so something else woke this up.
			if (started && now === last) return;
			last = now;
			started = true;
			void move(element, { opacity: [0, 1], y: [ARRIVAL_RISE, 0] }, { pace: 'fast' });
		});
	};
}

/* ---------------------------------------------------------------------------------------------
 * Sliding a list that has been rearranged.
 *
 * Measure where the rows are, change the list, measure again, and animate each row from where it
 * was to where it now is. Without it a reordered list simply is in the new order on the next frame,
 * and nobody can see which row moved or where it went, which is the one thing the animation is
 * for. Nothing is animated for its own sake here: the movement is the explanation.
 * ------------------------------------------------------------------------------------------- */

/** Where a set of elements are right now, to be handed to `slide` once they have moved. */
export function measure(elements: Iterable<Element>): Map<Element, DOMRect> {
	const seen = new Map<Element, DOMRect>();
	for (const element of elements) seen.set(element, element.getBoundingClientRect());
	return seen;
}

/**
 * Slide each element from where `measure` last saw it to where it is now.
 *
 * Call it after the DOM has been updated. An element that has not moved is skipped, and one that
 * was not measured is left alone: a row that has just appeared has nowhere to come from, and
 * inventing a start position for it would be an animation that says something untrue.
 */
export function slide(before: Map<Element, DOMRect>, options: MoveOptions = {}): void {
	if (motion.reduced) return;

	for (const [element, was] of before) {
		if (!element.isConnected) continue;
		const now = element.getBoundingClientRect();
		const dx = was.left - now.left;
		const dy = was.top - now.top;
		// Sub-pixel differences are the browser rounding, not a move anybody made.
		if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;

		void move(element, { x: [dx, 0], y: [dy, 0] }, { pace: 'fast', ...options });
	}
}

/* ---------------------------------------------------------------------------------------------
 * Arriving, and leaving.
 *
 * Everything above animates something already on the page. This is the other half: a toast, a
 * dialog, the selection bar, the drop overlay and the suggestions arriving and leaving, so
 * something says where they came from and that they have gone.
 *
 * ## Why these are declared on the element rather than run by `move`
 *
 * `move` cannot do a departure. By the time a script could animate something out, the framework
 * has already taken it off the page. Holding it there until an animation finishes is the
 * framework's job, and only the framework can do it. So arrival and departure are declared on the
 * element, in the one mechanism that owns when it exists, and every number comes from here so
 * there is still one set of durations and curves in this application rather than two.
 *
 * They compile to a real keyframe animation, which is why this is the right tool and not a
 * compromise: the browser runs those off the main thread, where a script setting a property every
 * frame is competing with everything else the page is doing.
 * -------------------------------------------------------------------------------------------
 */

/* Solving a cubic bezier for y at a given x.
 *
 * The stylesheet states its curves as `cubic-bezier(...)`, and a transition needs the same curve
 * as a function. Written out rather than swapped for the nearest ready-made easing: the closest
 * standard curve to this app's is visibly softer, and a second set of curves that almost matches
 * the first is exactly the drift this file exists to prevent.
 *
 * Newton-Raphson from a linear guess, which lands in a few passes over the range an easing is
 * defined on, with a bisection fallback for the flat parts where the slope is too small to divide
 * by.
 */
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

/** What the framework needs to run a transition. Written out rather than imported, so this module
 *  keeps owning its own vocabulary and does not widen what it depends on for the sake of a type. */
interface Transition {
	duration: number;
	easing: (t: number) => number;
	css: (t: number, u: number) => string;
}

interface ArrivalOptions extends MoveOptions {
	/** How far it travels, in px. Positive rises from below; negative drops from above. */
	y?: number;
	/**
	 * The same sideways. Positive comes in from the right; negative from the left.
	 *
	 * For a walk somebody can go both ways along (the first-run steps), where the direction
	 * of travel is the one thing the movement is there to say. Vertical arrival says "this is new";
	 * horizontal says "this is the next one along", and going back plays it the other way.
	 */
	x?: number;
	/** What size it starts at. 1 is no scaling. */
	scale?: number;
}

/** Which half of a transition the framework is asking for. */
interface Direction {
	direction?: 'in' | 'out' | 'both';
}

/**
 * The framework asks a transition for its shape once per direction when it is handed a function
 * rather than a shape, which is how one declaration arrives one way and leaves another.
 */
type Directed = (how?: Direction) => Transition;

/**
 * Something arriving, and leaving again.
 *
 * One declaration covers both directions, and the element is held on the page long enough to be
 * seen leaving, which is the half no script can do.
 *
 * The two halves are not mirror images. Arriving travels and fades at the pace asked for, on the
 * entrance curve; leaving only fades, one pace quicker (`EXIT_PACE`), on the exit curve. An exit
 * that retraced the whole entrance would make every dismissal as slow as the thing appearing.
 *
 * Reduced motion keeps the fade and drops the travel: the same rule `move` follows, for the same
 * reason. A shorter slide is still a slide, and what was asked for was no sliding.
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

/** How far a small surface rises as it opens, in px. The same distance a stylesheet's `rise` travels. */
const SURFACE_RISE = 4;

/**
 * A small surface opening: a menu, a popover, a toast, a tooltip.
 *
 * Fades and rises a few pixels at `fast`, and closes at `instant`. Named rather than spelled out by
 * each caller, so every small surface in the app opens and closes the same way.
 */
export function surface(node: Element): Directed {
	return arrive(node, { pace: 'fast', y: SURFACE_RISE });
}

/** How far a panel hanging from the top bar drops as it opens, in px: the Filter panel's
 *  `--space-6`, read from the stylesheet so the two cannot come apart. */
function barDrop(): number {
	const known = timings.get('--space-6');
	if (typeof known === 'number') return known;
	const read = Number.parseFloat(readToken('--space-6'));
	const value = Number.isFinite(read) && read > 0 ? read : 24;
	timings.set('--space-6', value);
	return value;
}

/** Room left round a dropping panel's clip for its shadow, which falls well below it. */
const SHADOW_ROOM = 80;

/**
 * A panel hanging from the top bar, coming out from under the bar and going back up under it:
 * the motion the Filter panel has, for every panel the bar drops (Sort by and a screen's own
 * menus beside it, and Add's).
 *
 * The Filter panel is a drawer in the page: its row opens from nothing to its full height over
 * `base` on the default curve, and the panel inside drops the last `--space-6` onto its place on
 * the spring over `slow`, seen only through the opening row, so it comes out from under the bar
 * and settles. Shutting takes both back over `base` on the default curve. A floating panel has no
 * row to open, so this draws the row's window as a clip on the panel itself: fixed where the
 * panel's top edge comes to rest, opening downwards as the drawer's row does, while the panel
 * drops through it. The two halves keep their own paces and curves inside one transition, which
 * is why the transition runs linear and each half eases its own share.
 *
 * Nothing fades while motion is on, as nothing fades on the Filter panel: it is revealed. Reduced
 * motion is the fade alone, the rule every movement here follows.
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

		/* `above`: how far over its resting place the panel is; `open`: how much of the window is. */
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

/** The veil behind something that arrived. It fades, and it never travels: a dimming that slides
 *  is a second thing moving behind the thing somebody is meant to be looking at. At `base`, the pace
 *  a sheet opens at, so a dialog and its veil arrive together and leave together. */
export function veil(node: Element, options: MoveOptions = {}): Directed {
	return arrive(node, { pace: 'base', ...options });
}

/** An edge of the screen a surface can stand on and come from. `end` is the trailing edge. */
export type Edge = 'foot' | 'end';

/**
 * A surface that stands on an edge of the screen coming out of that edge, and going back into it.
 *
 * The phone's two: a sheet standing on the foot (a menu, a chooser, the Info sheet) rises the whole
 * of its own height out of it, and a page pushed over another (a Settings section over More) comes
 * in from the trailing edge, the way a phone pushes a page. Unlike `arrive`, the leaving half
 * travels too, back the way it came, and it is still one pace quicker than the arrival
 * (`EXIT_PACE`) on the exit curve: something that stands on an edge and fades where it stands says
 * it has gone, where one that goes back into the edge says where. The whole of its own size rather
 * than a few pixels, because the travel is the point: a small rise from the foot of the screen reads
 * as a flicker at the bottom of it.
 *
 * Reduced motion keeps the fade and drops the travel, the rule every movement here follows.
 */
export function fromEdge(node: Element, options: { edge?: Edge; pace?: Pace } = {}): Directed {
	const { edge = 'foot', pace = 'base' } = options;
	/* The trailing edge is the right one in a left-to-right page and the left one otherwise. */
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
 * The file viewer growing out of the tile it was opened from, and shrinking back into it.
 *
 * The shared-element zoom the design keeps for this one moment: the surface starts at the size and
 * the place of what was pressed and grows to its own, so where it came from is answered without a
 * word, and closing shrinks it back to the place it came from as it fades. `from` is where that
 * was, measured by the caller when the movement starts; with nothing to grow from (the tile is off
 * the screen, or the viewer was opened by an address) it grows from a little smaller than itself in
 * place. One scale for both axes, so the picture is never squashed on
 * the way: a tile and a screen are different shapes, and the fade carries the difference.
 *
 * Arrives at `slow`, the one movement at that pace, and leaves one pace quicker on the exit curve.
 * Reduced motion is the fade alone.
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

/** How small the viewer starts when there is no tile to grow out of. */
const GROW_FROM = 0.88;

/**
 * A block of the page OPENING AND CLOSING, taking the room with it.
 *
 * The framework's own `slide`, with this app's pace and curve on it rather than the library's
 * defaults, and with reduced motion answered the way every other movement here answers it.
 *
 * ## Why it is not `arrive`
 *
 * `arrive` fades and travels: the element occupies its space for the whole of the movement, which is
 * right for something appearing OVER the page. A disclosure is the other thing entirely: what has
 * to move is the height, because everything below it has to come up as it closes. An opacity fade on
 * a block that keeps its full height is a block that vanishes and leaves a hole.
 *
 * ## Why the framework's and not one written here
 *
 * The height is the easy half. The padding, the margins and the borders all have to come off with it
 * or the block collapses to a band of its own spacing, and the element's `overflow` has to be taken
 * over for the duration and given back. That is what `slide` already does, correctly, and a second
 * copy of it here would be a second copy to keep right.
 *
 * `slow` by default: this is a large area of the screen opening, and the travel is the whole of what
 * says the block came from up there rather than simply appearing. Anything faster reads as a jump.
 *
 * Aliased on the way in because this module already exports a `slide` of its own, which is a
 * different thing: rows moving to new places in a list that was rearranged.
 */
export function reveal(node: Element, options: MoveOptions = {}): Transition {
	const { pace = 'slow' } = options;
	return revealSlide(node as HTMLElement, {
		// Zero rather than capped: a disclosure that still slides, only faster, is still a slide, and
		// what was asked for was no sliding. The block is simply there or simply not.
		duration: motion.reduced ? 0 : motion.duration(paceMs(pace)),
		easing: bezier(easingToken('--ease', [0.2, 0, 0, 1]))
	}) as Transition;
}

/**
 * The rest of a list closing up after one of its entries has gone.
 *
 * `slide` above does this for a list the application rearranges itself, by measuring around its
 * own change. A list the framework maintains never gives anyone that moment (the entry is gone
 * and the survivors are already in their new places), so the framework hands over where each one
 * was instead, and this animates the difference from there.
 *
 * Same job as `slide`, same reason, different half of the application. Without it, dismissing the
 * top of three toasts snaps the two below it up a row, which reads as the wrong one having gone.
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

/* ---------------------------------------------------------------------------------------------
 * A figure counting up to its value.
 *
 * A number on screen never moves, with one exception: Insights' figures count up once when a
 * period's answer arrives, so a page of totals reads as having just been added up rather than as a
 * table that was already there. Everywhere else a count changing in place is a fact changing, and
 * animating it would make the reader wait to learn what it now says. The test beside this module
 * holds the exception to its one reader.
 * ------------------------------------------------------------------------------------------- */

/**
 * Draw a count rising from zero to `to` over `--dur-ambient` on the entrance curve, then `to`.
 *
 * The whole number rises, rather than each digit turning over on its own, because a number whose
 * digits spin independently cannot be read until every one of them stops. Reduced motion, or
 * nothing to count, draws `to` at once. Returns a stop, for a caller whose figure is replaced
 * before the count has finished.
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
