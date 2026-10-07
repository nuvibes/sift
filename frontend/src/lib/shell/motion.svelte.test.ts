/* What the app does when it has been asked not to move.
 *
 * The claim is specific and easy to get wrong in the direction that looks fine: reduced motion is
 * not "the same animation, faster". Transforms go entirely and opacity stays, so a thing that slid
 * in now appears, and a thing that only slid does nothing at all. A shorter slide is still a slide.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import { flushSync } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
	appears,
	arrive,
	countUp,
	fromBar,
	surface,
	motion,
	move,
	reflow,
	slide,
	measure,
	veil,
	loadMotionPreference,
	setMotionPreference,
	REDUCED_CAP_MS
} from './motion.svelte';

const animate = vi.hoisted(() => vi.fn(() => Promise.resolve()));
vi.mock('motion', () => ({ animate }));

beforeEach(() => {
	animate.mockClear();
	motion.preference = 'system';
});

afterEach(() => {
	motion.preference = 'system';
});

/** What the library was asked to animate, and how. */
function asked() {
	expect(animate).toHaveBeenCalledOnce();
	const [element, keyframes, options] = animate.mock.calls[0] as unknown as [
		Element,
		Record<string, unknown>,
		Record<string, number>
	];
	return { element, keyframes, options };
}

describe('who wins, the person or the machine', () => {
	/* THE HALF THAT IS CSS.
	 *
	 * Roughly half of Sift's movement is CSS, and a media query asking the operating system
	 * cannot be un-applied by anything. So a rule keyed on one would go on killing every
	 * transition with `!important` while the screen offering "Full motion" said it was
	 * animating. A switch that lies is worse than no switch.
	 *
	 * The answer is stamped on the root element and the stylesheet keys on THAT, so these
	 * assertions are about the attribute rather than about any one rule: it is the one thing
	 * every one of those rules reads.
	 */
	function stamped(): string | undefined {
		return document.documentElement.dataset.motion;
	}

	it('lets somebody ask for movement even where the machine asked for less', () => {
		setMotionPreference('full');
		expect(motion.reduced).toBe(false);
		expect(stamped()).toBe('full');
	});

	it('lets somebody ask for stillness even where the machine did not', () => {
		setMotionPreference('reduce');
		expect(motion.reduced).toBe(true);
		expect(stamped()).toBe('reduce');
	});

	it('follows the machine when asked to, and says so where the stylesheet can read it', () => {
		setMotionPreference('system');
		expect(stamped()).toBe(motion.reduced ? 'reduce' : 'full');
	});

	it('remembers the choice for this browser, and reads it back', () => {
		setMotionPreference('reduce');
		motion.preference = 'full'; // as a fresh page load would leave it, before loading
		loadMotionPreference();
		expect(motion.preference).toBe('reduce');
		expect(stamped()).toBe('reduce');
	});

	it('animates on a browser that has never been told anything', () => {
		// Deliberate: an operating system turns its reduced-motion preference on for reasons that have
		// nothing to do with the person, and an application that arrives silent looks broken.
		localStorage.clear();
		loadMotionPreference();
		expect(motion.preference).toBe('full');
	});
});

describe('an ordinary movement', () => {
	it('is handed to the library in seconds, because that is what it counts in', async () => {
		// The app counts in milliseconds, like its stylesheet. The conversion belongs in one place.
		await move(document.createElement('div'), { y: [8, 0], opacity: [0, 1] });

		const { keyframes, options } = asked();
		expect(keyframes).toEqual({ y: [8, 0], opacity: [0, 1] });
		expect(options.duration).toBeGreaterThan(0);
		expect(options.duration).toBeLessThan(1);
	});
});

describe('when motion is off', () => {
	beforeEach(() => {
		motion.preference = 'reduce';
	});

	it('drops the transform and keeps the fade', async () => {
		await move(document.createElement('div'), { y: [8, 0], opacity: [0, 1] });

		expect(asked().keyframes).toEqual({ opacity: [0, 1] });
	});

	it('and does not animate at all when the movement was only a transform', async () => {
		/* Nothing is left once the transform goes, and a nothing-long animation of nothing is still a
		 * frame the browser schedules. The element is simply where it was going. */
		await move(document.createElement('div'), { x: [20, 0] });

		expect(animate).not.toHaveBeenCalled();
	});

	it('and caps what it does run', async () => {
		await move(document.createElement('div'), { opacity: [0, 1] }, { pace: 'slow' });

		expect(asked().options.duration).toBeLessThanOrEqual(REDUCED_CAP_MS / 1000);
	});

	it('and a rearranged list does not slide at all', () => {
		// The whole point of the slide is showing which row moved where. With motion off there is no
		// movement to explain, so there is nothing to say.
		const element = document.createElement('div');
		document.body.append(element);

		slide(measure([element]));

		expect(animate).not.toHaveBeenCalled();
		element.remove();
	});
});

describe('sliding a list that has been rearranged', () => {
	it('leaves alone anything that did not move', () => {
		/* Sub-pixel differences are the browser rounding rather than a move anybody made, and an
		 * animation of one is a row twitching for no reason. */
		const element = document.createElement('div');
		document.body.append(element);

		slide(measure([element]));

		expect(animate).not.toHaveBeenCalled();
		element.remove();
	});

	it('and anything that has left the page since it was measured', () => {
		// A row put away is measured and then gone. Animating it is animating a detached element.
		const element = document.createElement('div');
		document.body.append(element);
		const before = measure([element]);
		element.remove();

		slide(before);

		expect(animate).not.toHaveBeenCalled();
	});
});

/** The half the framework asks for as something arrives. */
function arriving(options?: Parameters<typeof arrive>[1]) {
	return arrive(document.createElement('div'), options)({ direction: 'in' });
}

/** And the half it asks for as the same thing leaves. */
function leaving(options?: Parameters<typeof arrive>[1]) {
	return arrive(document.createElement('div'), options)({ direction: 'out' });
}

describe('arriving and leaving', () => {
	/* These are declared on an element rather than run by a script, because only the framework can
	 * hold something on the page long enough to be seen leaving. What is asserted is the shape they
	 * hand back: how long, on what curve, and what is actually being animated at a given moment. */

	it('travels and fades, and ends where it started', () => {
		const { css } = arriving({ y: 16 });

		// At the start of an arrival: invisible, and the full distance away.
		expect(css(0, 1)).toContain('opacity: 0');
		expect(css(0, 1)).toContain('translateY(16px)');
		// At the end: solid, and home.
		expect(css(1, 0)).toContain('opacity: 1');
		expect(css(1, 0)).toContain('translateY(0px)');
	});

	it('scales from a size when it is asked to', () => {
		const { css } = arriving({ scale: 0.5 });

		expect(css(0, 1)).toContain('scale(0.5)');
		expect(css(1, 0)).toContain('scale(1)');
	});

	it('drops the travel and keeps the fade when motion is off', () => {
		/* The same rule the scripted half follows. A shorter slide is still a slide, and what was
		 * asked for was no sliding. So the transform is not shortened, it is not there. */
		motion.preference = 'reduce';

		const { css, duration } = arriving({ y: 16, scale: 0.9 });

		expect(css(0, 1)).toBe('opacity: 0');
		expect(css(0, 1)).not.toContain('transform');
		expect(duration).toBeLessThanOrEqual(REDUCED_CAP_MS);
	});

	it('and a veil never travels even when it could', () => {
		// A dimming that slides is a second thing moving behind the thing being looked at.
		const { css, duration } = veil(document.createElement('div'))({ direction: 'in' });

		expect(css(0.5, 0.5)).not.toContain('transform');
		expect(duration, 'A veil arrives at the pace a sheet does.').toBe(200);
	});

	it('leaves by fading only, never retracing the way it came', () => {
		const { css } = leaving({ y: 16, scale: 0.9 });

		expect(css(0.5, 0.5)).toBe('opacity: 0.5');
	});

	it('and leaves one pace quicker than it arrived', () => {
		// Instant 90, fast 140, base 200, slow 320: the fallbacks, since the test document has no
		// stylesheet to read the tokens from.
		expect(leaving({ pace: 'fast' }).duration).toBe(90);
		expect(leaving({ pace: 'base' }).duration).toBe(140);
		expect(leaving({ pace: 'slow' }).duration).toBe(200);
	});

	it('on the exit curve, which starts slowly and leaves quickly', () => {
		const { easing } = leaving();

		expect(easing(0)).toBe(0);
		expect(easing(1)).toBe(1);
		expect(
			easing(0.5),
			'The entrance curve is well past halfway here; this one is not.'
		).toBeLessThan(0.5);
	});

	it('opens a small surface with a rise of a few pixels at the fast pace, and closes it instantly', () => {
		const opening = surface(document.createElement('div'))({ direction: 'in' });
		const closing = surface(document.createElement('div'))({ direction: 'out' });

		expect(opening.duration).toBe(140);
		expect(opening.css(0, 1)).toContain('translateY(4px)');
		expect(closing.duration).toBe(90);
		expect(closing.css(0.5, 0.5)).not.toContain('transform');
	});
});

describe('a panel hanging from the top bar', () => {
	/* Sort by's list and Add's panel move as the Filter panel beside them does: out from under the
	 * bar, dropping `--space-6` onto their place on the spring while a window fixed at their top edge
	 * opens downwards, and back up under it over the base pace. The fallbacks stand in for the
	 * tokens: the test document has no stylesheet. */
	const opening = () => fromBar(document.createElement('div'))({ direction: 'in' });
	const closing = () => fromBar(document.createElement('div'))({ direction: 'out' });

	it('starts hidden under the bar, a drop above its place, and ends home and whole', () => {
		const { css, duration } = opening();

		expect(duration, 'The drop takes the slow pace, as the Filter panel does.').toBe(320);
		expect(css(0, 1)).toContain('translateY(-24px)');
		expect(css(0, 1), 'Nothing of it shows yet: the window is shut.').toContain(
			'inset(24px -80px calc(100% - 24px)'
		);
		expect(css(1, 0)).toContain('translateY(0px)');
		expect(css(1, 0), 'And all of it at the end, its shadow included.').toContain(
			'calc(0% - 80px)'
		);
		expect(css(0.5, 0.5)).not.toContain('opacity');
	});

	it('comes down on the spring, settling past its place, while the window opens at the base pace', () => {
		const { css } = opening();
		const at = (t: number) => Number(/translateY\((-?[\d.]+)px\)/.exec(css(t, 1 - t))?.[1]);
		const frames = Array.from({ length: 21 }, (_, i) => at(i / 20));

		expect(Math.max(...frames), 'The spring carries it a little past its place.').toBeGreaterThan(
			0
		);
		expect(css(200 / 320, 1 - 200 / 320), 'The window is fully open by the base pace.').toContain(
			'calc(0% -'
		);
	});

	it('goes back up under the bar over the base pace, on the default curve', () => {
		const { css, duration, easing } = closing();

		expect(duration).toBe(200);
		expect(easing(0.5), 'The default curve is well past halfway here.').toBeGreaterThan(0.5);
		expect(css(0, 1)).toContain('translateY(-24px)');
		expect(css(0, 1)).toContain('calc(100% - 24px)');
	});

	it('only fades when motion is off', () => {
		motion.preference = 'reduce';

		expect(opening().css(0.5, 0.5)).toBe('opacity: 0.5');
		expect(closing().css(0.5, 0.5)).toBe('opacity: 0.5');
		expect(opening().duration).toBeLessThanOrEqual(REDUCED_CAP_MS);
	});
});

describe('the curve the stylesheet states', () => {
	/* The transitions need the app's own easing as a function. A ready-made one from a library is
	 * not the same curve (the nearest standard easing to this app's is visibly softer), and two
	 * curves that almost match is the drift the module exists to prevent. */

	it('is pinned at both ends', () => {
		const { easing } = arriving();

		expect(easing(0)).toBe(0);
		expect(easing(1)).toBe(1);
	});

	it('never goes backwards', () => {
		const { easing } = arriving();

		let last = -1;
		for (let step = 0; step <= 20; step += 1) {
			const value = easing(step / 20);
			expect(value).toBeGreaterThanOrEqual(last);
			last = value;
		}
	});

	it('and front-loads the movement, which is what this app means by its default curve', () => {
		// The app's curve puts most of the distance early and settles: at the halfway point it is
		// well past halfway. A linear or symmetric curve would sit at about a half here.
		const { easing } = arriving();

		expect(easing(0.5)).toBeGreaterThan(0.7);
	});
});

describe('a list closing up after one of its entries has gone', () => {
	it('animates from where the framework says each survivor was', () => {
		const from = new DOMRect(0, 100, 10, 10);
		const to = new DOMRect(0, 60, 10, 10);

		const { css } = reflow(document.createElement('div'), { from, to });

		// It starts 40px below where it now is, and travels up to nothing.
		expect(css(0, 1)).toContain('translate(0px, 40px)');
		expect(css(1, 0)).toContain('translate(0px, 0px)');
	});

	it('and does not move at all when motion is off', () => {
		motion.preference = 'reduce';

		const { duration } = reflow(document.createElement('div'), {
			from: new DOMRect(0, 100, 10, 10),
			to: new DOMRect(0, 60, 10, 10)
		});

		expect(duration).toBe(0);
	});
});

/*
 * Content arriving.
 *
 * The interesting half of this is not the fade. It is WHEN it runs. It has to run again when the
 * page turns, because the element is the same element holding different files; it must not run while
 * the next page is still being fetched, because a fade over the page somebody is leaving explains
 * nothing; and it must not run because something unrelated woke the effect up, or a wall would fade
 * every time the window was dragged wider.
 */
describe('content arriving', () => {
	/** Run the attachment the way a component would, and hand back how to stop it. */
	function attach(element: Element, key?: () => unknown) {
		const stop = $effect.root(() => appears(key)(element));
		flushSync();
		return stop;
	}

	it('fades in and rises the first time it is drawn', () => {
		const stop = attach(document.createElement('div'));

		const { keyframes, options } = asked();
		expect(keyframes.opacity).toEqual([0, 1]);
		expect(keyframes.y).toEqual([6, 0]);
		// `--dur-fast`, the duration anything appearing takes. Seconds, because the library counts
		// in them and the app counts in milliseconds.
		expect(options.duration).toBeCloseTo(0.14);

		stop();
	});

	it('runs again when the page it is showing changes', () => {
		let showing = $state('0:200');
		const stop = attach(document.createElement('div'), () => showing);

		expect(animate).toHaveBeenCalledOnce();

		showing = '40:200';
		flushSync();
		expect(animate).toHaveBeenCalledTimes(2);

		stop();
	});

	it('does not run while the next page is still in flight', () => {
		let showing = $state<string | null>(null);
		const stop = attach(document.createElement('div'), () => showing);

		expect(
			animate,
			'A fade played over the page being left behind describes nothing.'
		).not.toHaveBeenCalled();

		showing = '0:200';
		flushSync();
		expect(animate).toHaveBeenCalledOnce();

		stop();
	});

	it('and not when something else wakes it with the same page on screen', () => {
		let showing = $state('0:200');
		let unrelated = $state(0);
		const stop = attach(document.createElement('div'), () => {
			// Standing in for the window being resized: read by the same computation, changing nothing
			// about what is on screen.
			void unrelated;
			return showing;
		});

		expect(animate).toHaveBeenCalledOnce();

		unrelated = 1;
		flushSync();
		expect(
			animate,
			'The wall fades on every drag of a window edge if the key is not compared.'
		).toHaveBeenCalledOnce();

		stop();
	});

	it('and keeps only the fade when motion is off', () => {
		motion.preference = 'reduce';
		const stop = attach(document.createElement('div'));

		const { keyframes } = asked();
		expect(keyframes.opacity).toEqual([0, 1]);
		expect(keyframes.y, 'A shorter rise is still a rise.').toBeUndefined();

		stop();
	});
});

describe('a figure counting up', () => {
	/* Frames are run by hand, each at a time the test names, so what is drawn at a given moment is
	 * exact rather than whatever the machine's clock happened to allow. */
	let frames: FrameRequestCallback[];

	beforeEach(() => {
		frames = [];
		vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
			frames.push(callback);
			return frames.length;
		});
		vi.stubGlobal('cancelAnimationFrame', () => {
			frames = [];
		});
	});

	afterEach(() => {
		vi.unstubAllGlobals();
	});

	function frameAt(ms: number): void {
		const next = frames.shift();
		expect(next, 'A frame was asked for.').toBeDefined();
		next?.(ms);
	}

	it('rises from zero to the figure over the ambient pace, and stops there', () => {
		motion.preference = 'full';
		const drawn: number[] = [];
		countUp(1240, (value) => drawn.push(value));

		expect(drawn).toEqual([0]);
		frameAt(1000);
		frameAt(1300);
		frameAt(1600);

		const [, start, middle, end] = drawn;
		expect(start).toBe(0);
		expect(middle).toBeGreaterThan(620);
		expect(middle).toBeLessThan(1240);
		expect(end).toBe(1240);
		expect(frames, 'Nothing is left running once the figure is reached.').toHaveLength(0);
	});

	it('draws the figure immediately when motion is off', () => {
		motion.preference = 'reduce';
		const drawn: number[] = [];
		countUp(1240, (value) => drawn.push(value));

		expect(drawn).toEqual([1240]);
		expect(frames).toHaveLength(0);
	});

	it('and when there is nothing to count', () => {
		motion.preference = 'full';
		const drawn: number[] = [];
		countUp(0, (value) => drawn.push(value));

		expect(drawn).toEqual([0]);
		expect(frames).toHaveLength(0);
	});

	it('stops when asked, so a figure replaced mid-count is not drawn over', () => {
		motion.preference = 'full';
		const drawn: number[] = [];
		const stop = countUp(1240, (value) => drawn.push(value));

		stop();
		expect(frames).toHaveLength(0);
		expect(drawn).toEqual([0]);
	});
});

describe('the one number in the app that moves', () => {
	/* A count changing in place on any other screen (a Browse total, a queue's length, a badge) is a
	 * fact changing, and it is drawn immediately. So the count-up has exactly one reader, and a
	 * second one is refused here. */
	const SOURCE = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

	function everyFile(dir: string): string[] {
		const found: string[] = [];
		for (const entry of readdirSync(dir)) {
			if (entry === 'node_modules' || entry.startsWith('.')) continue;
			const path = join(dir, entry);
			if (statSync(path).isDirectory()) found.push(...everyFile(path));
			else if (/\.(svelte|ts)$/.test(entry) && !entry.endsWith('.test.ts')) found.push(path);
		}
		return found;
	}

	it('is read by Insights and by nothing else', () => {
		const readers = everyFile(SOURCE)
			.map((path) => relative(SOURCE, path).split('\\').join('/'))
			.filter((where) => where !== 'lib/shell/motion.svelte.ts')
			.filter((where) => /\bcountUp\b/.test(readFileSync(join(SOURCE, where), 'utf8')));

		expect(readers).toEqual(['lib/components/charts/FigureCard.svelte']);
	});
});
