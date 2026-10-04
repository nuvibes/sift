/*
 * One row that scrolls sideways, with an arrow at each end while there is more that way.
 *
 * A module rather than a copy per strip: measuring the ends, nudging by one item, accelerating
 * while an arrow is held, and keeping those in step with the wheel, the keyboard, a change of
 * contents and a resize. The faces strip and the lookalikes strip read as a pair one under the
 * other, and one scrolling differently would read as broken.
 *
 * Not here: the scrolling itself and the sideways wheel, which are `Scroller horizontal`'s (most
 * mice have one wheel, and it goes up and down); and the arrow buttons, each strip's own markup
 * (the shared `Button` with that strip's words, such as "Earlier faces" and "More like this").
 *
 * `Scroller`'s own arrows are a different control, measuring `scrollTop` for a menu. What is shared
 * with them is the pace, `nudgeDelay`, so a held arrow on a strip accelerates exactly as one in a
 * chooser.
 */
import { nudgeDelay } from './scroll-nudge';

/* Half a pixel of slack at each end, because a scroll position is fractional on a display that is
   not at 100% and an arrow that will not go away at the end of a strip reads as broken. */
const AT_THE_END = 0.5;

export class SideScroll {
	/** The scrolling box, once the `Scroller` inside the strip has handed it over. */
	#box = $state<HTMLElement | null>(null);
	/** Whether there is strip that way. Each is a button that exists only while it is true. */
	canBack = $state(false);
	canOn = $state(false);

	/* The hold in progress. A timer rather than an interval because the wait between nudges
	   CHANGES. See `scroll-nudge.ts`, which owns that easing for every arrow in the app. */
	#held: ReturnType<typeof setTimeout> | null = null;

	/** Take the viewport from `Scroller`'s `onviewport`. */
	readonly take = (element: HTMLElement | null): void => {
		this.#box = element;
	};

	/** Where the strip stands, both ends at once. */
	measure(): void {
		const box = this.#box;
		if (box === null) return;
		this.canBack = box.scrollLeft > AT_THE_END;
		this.canOn = box.scrollLeft < box.scrollWidth - box.clientWidth - AT_THE_END;
	}

	/*
	 * What one nudge is worth: an ITEM, measured, not a number written here.
	 *
	 * The items are one size within a strip and a different size between strips: a face crop is
	 * square and a lookalike tile is as wide as the file's own shape, so asking the strip what its
	 * first item measures is the answer that stays right for both, and stays right if either changes.
	 */
	#oneItem(box: HTMLElement): number {
		const first = box.querySelector<HTMLElement>('li');
		return first?.offsetWidth ?? box.clientWidth;
	}

	/** Stop a hold. Safe to call when nothing is held, which is what a pointer leaving does. */
	readonly stop = (): void => {
		if (this.#held !== null) clearTimeout(this.#held);
		this.#held = null;
	};

	/**
	 * Move one item that way, and go on moving while it is held.
	 *
	 * One item on arrival, then the eased wait: a pointer crossing the arrow on its way somewhere
	 * else moves the strip by exactly one item.
	 */
	readonly nudge = (way: -1 | 1): void => {
		this.stop();
		let tick = 0;
		const step = () => {
			const box = this.#box;
			if (box === null) return;
			box.scrollLeft += way * this.#oneItem(box);
			this.measure();
			// Stopping at the end rather than going on ringing a timer nobody can see.
			if (way < 0 ? !this.canBack : !this.canOn) return;
			this.#held = setTimeout(step, nudgeDelay(tick++));
		};
		step();
	};

	/**
	 * Keep the two answers in step with the strip. Call inside an `$effect` and return what it gives
	 * back, which is that effect's cleanup.
	 *
	 * The strip changes under this from three directions: the wheel and the keyboard move it, a
	 * different subject is a different number of items, and the box itself is resized by the window.
	 * The child is observed as well as the box, because a run of items that grows is a change in
	 * what can be scrolled to without the box moving at all.
	 */
	watch(): (() => void) | undefined {
		const box = this.#box;
		if (box === null) return undefined;
		this.measure();
		const again = () => this.measure();
		box.addEventListener('scroll', again, { passive: true });
		const watching = new ResizeObserver(again);
		watching.observe(box);
		const inside = box.firstElementChild;
		if (inside !== null) watching.observe(inside);
		return () => {
			box.removeEventListener('scroll', again);
			watching.disconnect();
			this.stop();
		};
	}
}
