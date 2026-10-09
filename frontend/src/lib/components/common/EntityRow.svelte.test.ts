/* When the row offers its way out, measured, with the two widths supplied on the prototype
 * (jsdom lays nothing out); a real overflow is checked on the gallery. */
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
	/* Only the two measured elements; an open line wraps, so the run no longer overflows. */

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

		// The glyph is the heading, named to a screen reader and in the tooltip.
		const glyph = host.querySelector('[role="img"]');
		expect(glyph?.getAttribute('aria-label')).toBe('Tags');
		/* At 20, the size beside a chip. */
		expect(glyph?.querySelector('.icon')?.classList.contains('size-20')).toBe(true);
		expect([...host.querySelectorAll('.probe-chip')].map((one) => one.textContent)).toEqual([
			'rooftop',
			'rainy'
		]);
	});

	it('offers no way out when nothing is hidden', () => {
		// No word offering what is already on screen.
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
		/* Measured only while closed, or the word would vanish when pressed. */
		boxWidth = 120;
		runWidth = 500;
		draw({ words: ['Marla Quist', 'Ines Dray', 'Tobias Renn'] });

		wayOut()?.click();
		flushSync();
		reported();
		expect(wayOut()?.textContent?.trim()).toBe('See fewer');

		// Measured again once put back.
		wayOut()?.click();
		flushSync();
		reported();
		expect(wayOut()?.textContent?.trim()).toBe('See all');
	});

	it('keeps the adder OUT of the line that clips', () => {
		/* The adder stays outside the clipped chips. */
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
