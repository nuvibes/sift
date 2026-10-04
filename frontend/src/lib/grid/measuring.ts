/*
 * Support for the tests, and only for the tests: the browser MEASURING a wall of cards.
 *
 * jsdom lays nothing out (every box is zero), and has no ResizeObserver, so a mounted wall never
 * learns its card size and every request it makes is at the unmeasured fallback. That hides the
 * one moment the first-page fault lives in: the first card drawing, the size changing, and the wall
 * asking again. This stands in for both halves, the way `cards-first-page.svelte.test.ts` does for a
 * bare `CardPaging`, so a test mounting a whole wall can count what it asks for across that moment.
 *
 * Every box answers the card's size, the scrolling one included, so a screen holds one row of
 * cards; its width is whatever `clientWidth` the test has given every element.
 *
 * Nothing in the app imports this, and it is not in the bundle.
 */

/** Install the stand-ins. `deliver` is the browser laying the page out; `done` puts jsdom back. */
export function measuring(card: { width: number; height: number }) {
	/* Each observer with what it watches: a real observer hands its callback one entry per
	   watched element, and a library's shared observer walks those entries. */
	const observers: Array<{
		callback: (entries: ResizeObserverEntry[]) => void;
		targets: Set<Element>;
	}> = [];
	const hadObserver = (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
	const hadRect = HTMLElement.prototype.getBoundingClientRect;
	(globalThis as { ResizeObserver?: unknown }).ResizeObserver = class {
		#targets = new Set<Element>();
		constructor(callback: (entries: ResizeObserverEntry[]) => void) {
			observers.push({ callback, targets: this.#targets });
		}
		observe(target: Element) {
			this.#targets.add(target);
		}
		unobserve(target: Element) {
			this.#targets.delete(target);
		}
		disconnect() {
			this.#targets.clear();
		}
	};
	HTMLElement.prototype.getBoundingClientRect = function (this: HTMLElement) {
		const { width, height } = card;
		return {
			width,
			height,
			top: 0,
			left: 0,
			right: width,
			bottom: height,
			x: 0,
			y: 0
		} as DOMRect;
	};
	return {
		deliver() {
			for (const { callback, targets } of observers) {
				callback(
					[...targets].map(
						(target) =>
							({ target, contentRect: target.getBoundingClientRect() }) as ResizeObserverEntry
					)
				);
			}
		},
		done() {
			(globalThis as { ResizeObserver?: unknown }).ResizeObserver = hadObserver;
			HTMLElement.prototype.getBoundingClientRect = hadRect;
		}
	};
}
