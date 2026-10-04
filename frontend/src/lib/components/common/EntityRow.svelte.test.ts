/*
 * One kind of thing a file belongs to, as a row.
 *
 * What is pinned is when the way out is offered. It is measured rather than counted (a count would
 * offer "See all" over three chips on a wide window and hide four on a narrow one), and a
 * measurement can silently stop working because nothing on screen says whether it ran.
 *
 * jsdom lays nothing out: every box is zero wide and `ResizeObserver` reports nothing (see
 * `test-setup`). So the two widths are supplied on the prototype before the row is drawn, which is
 * enough because the row measures on mount as well as on every report. Noticing a real overflow is
 * a browser's business, checked on the gallery specimen.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount, type ComponentProps } from 'svelte';

import Probe from './EntityRowProbe.test.svelte';

/** What the clipping box and the run of chips inside it will measure, this test. */
let boxWidth = 0;
let runWidth = 0;

const realRect = Element.prototype.getBoundingClientRect;
const realClientWidth = Object.getOwnPropertyDescriptor(Element.prototype, 'clientWidth');
const realObserver = globalThis.ResizeObserver;

/** Every callback the row handed to a `ResizeObserver` while this fake is installed. */
let reporters: ResizeObserverCallback[] = [];

/** Tell the row its boxes moved, which is what a browser does when a row wraps. */
function reported() {
	for (const report of reporters) {
		report([] as unknown as ResizeObserverEntry[], null as unknown as ResizeObserver);
	}
	flushSync();
}

let host: HTMLElement;
let instance: ReturnType<typeof mount> | null = null;

beforeEach(() => {
	boxWidth = 0;
	runWidth = 0;
	reporters = [];
	/* Only the two elements the row measures. Everything else keeps jsdom's own answer, so a
	   component drawn inside this one cannot accidentally be handed a width.

	   The run answers differently once the line is OPEN, because that is what a real one does: an
	   open line wraps, so the run stops being wider than the box it is in. Without that, this
	   environment could not tell a frozen measurement from a live one. */
	Object.defineProperty(Element.prototype, 'clientWidth', {
		configurable: true,
		get(this: Element) {
			return this.classList.contains('line') ? boxWidth : 0;
		}
	});
	Element.prototype.getBoundingClientRect = function rect(this: Element) {
		if (this.classList.contains('run')) {
			const wrapped = this.parentElement?.classList.contains('open') ?? false;
			return { width: wrapped ? boxWidth : runWidth } as DOMRect;
		}
		return realRect.call(this);
	};
	globalThis.ResizeObserver = class {
		constructor(report: ResizeObserverCallback) {
			reporters.push(report);
		}
		observe(): void {}
		unobserve(): void {}
		disconnect(): void {}
	} as unknown as typeof ResizeObserver;
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	host.remove();
	Element.prototype.getBoundingClientRect = realRect;
	if (realClientWidth) Object.defineProperty(Element.prototype, 'clientWidth', realClientWidth);
	globalThis.ResizeObserver = realObserver;
});

function draw(props: ComponentProps<typeof Probe>) {
	instance = mount(Probe, { target: host, props });
	flushSync();
}

/** The way out, whatever it currently says. Null when the row is not offering one. */
function wayOut(): HTMLButtonElement | null {
	return host.querySelector('.entity-row button.btn');
}

describe('EntityRow', () => {
	it('draws the kind as a named glyph in front of the chips it was given', () => {
		draw({ icon: 'shoppingmode', label: 'Tags', words: ['rooftop', 'rainy'] });

		// The glyph is the whole heading: five words down the left of a dialog is a column of labels
		// beside a column of chips, so the name is said to a screen reader and in the tooltip.
		const glyph = host.querySelector('[role="img"]');
		expect(glyph?.getAttribute('aria-label')).toBe('Tags');
		/* AND AT THE SIZE THAT STANDS BESIDE A CHIP. The box is `--chip-height` in the stylesheet,
		   which nothing here can read; what can be read is the sanctioned size the mark inside it is
		   drawn at, 20, as every other mark in the row is. */
		expect(glyph?.querySelector('.icon')?.classList.contains('size-20')).toBe(true);
		expect([...host.querySelectorAll('.probe-chip')].map((one) => one.textContent)).toEqual([
			'rooftop',
			'rainy'
		]);
	});

	it('offers no way out when nothing is hidden', () => {
		// A word offering to show what is already on screen is a control that does nothing, and it
		// would be on four rows out of five.
		boxWidth = 500;
		runWidth = 120;
		draw({ words: ['Marla Quist'] });

		expect(wayOut()).toBeNull();
	});

	it('offers See all when the chips do not fit, and puts the row back again', () => {
		boxWidth = 120;
		runWidth = 500;
		draw({ words: ['Marla Quist', 'Ines Dray', 'Tobias Renn'] });

		const out = wayOut();
		if (!out) throw new Error('nothing offers to show the rest');
		expect(out.textContent?.trim()).toBe('See all');
		expect(host.querySelector('.line.open')).toBeNull();

		out.click();
		flushSync();
		expect(host.querySelector('.line.open')).not.toBeNull();
		expect(wayOut()?.textContent?.trim()).toBe('See fewer');

		wayOut()?.click();
		flushSync();
		expect(host.querySelector('.line.open')).toBeNull();
		expect(wayOut()?.textContent?.trim()).toBe('See all');
	});

	it('keeps the way back while the row is open, though an open row measures as fitting', () => {
		/*
		 * The one thing that would be silent. An open row wraps, so measuring it answers "this
		 * fits", and the word would remove itself the instant it was pressed. The row is only ever
		 * measured while closed.
		 *
		 * Driven through a report rather than by waiting: a browser tells a component its boxes
		 * moved when a row wraps, and that report is exactly what must not be acted on here.
		 */
		boxWidth = 120;
		runWidth = 500;
		draw({ words: ['Marla Quist', 'Ines Dray', 'Tobias Renn'] });

		wayOut()?.click();
		flushSync();
		reported();
		expect(wayOut()?.textContent?.trim()).toBe('See fewer');

		// And it is measured again the moment the row is put back, rather than staying on the
		// frozen answer.
		wayOut()?.click();
		flushSync();
		reported();
		expect(wayOut()?.textContent?.trim()).toBe('See all');
	});

	it('keeps the adder OUT of the line that clips', () => {
		/* Among the chips it would be the first thing pushed out of sight on a row with a lot of
		   them: a control that disappears exactly when the row it belongs to is busiest. */
		boxWidth = 120;
		runWidth = 500;
		draw({ words: ['Marla Quist', 'Ines Dray'], adding: true });

		const adder = host.querySelector('.probe-adder');
		expect(adder).not.toBeNull();
		expect(adder?.closest('.line')).toBeNull();
	});

	it('draws no adder at all where the caller handed none', () => {
		// A guest may not tag anything, so the row must not draw an empty box where the button goes.
		draw({ words: ['Marla Quist'] });

		expect(host.querySelector('.adder')).toBeNull();
	});
});
