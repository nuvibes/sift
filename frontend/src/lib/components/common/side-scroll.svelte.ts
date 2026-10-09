/* One row scrolling sideways, with an arrow at each end while there is more; the scrolling and
 * the wheel are Scroller's, and the pace is `nudgeDelay`, shared with every arrow. */
import { nudgeDelay } from './scroll-nudge';

/* Half a pixel of slack: scroll positions are fractional at other zooms. */
const AT_THE_END = 0.5;

export class SideScroll {
	/** The scrolling box, once the `Scroller` inside the strip has handed it over. */
	#box = $state<HTMLElement | null>(null);
	/** Whether there is strip that way. Each is a button that exists only while it is true. */
	canBack = $state(false);
	canOn = $state(false);

	/* A timer, since the wait between nudges eases (scroll-nudge.ts). */
	#held: ReturnType<typeof setTimeout> | null = null;

	/** Take the viewport from `Scroller`'s `onviewport`. */
	readonly take = (element: HTMLElement | null): void => {
		this.#box = element;
	};

	measure(): void {
		const box = this.#box;
		if (box === null) return;
		this.canBack = box.scrollLeft > AT_THE_END;
		this.canOn = box.scrollLeft < box.scrollWidth - box.clientWidth - AT_THE_END;
	}

	/* One nudge is the first item, measured: items differ in width between strips. */
	#oneItem(box: HTMLElement): number {
		const first = box.querySelector<HTMLElement>('li');
		return first?.offsetWidth ?? box.clientWidth;
	}

	/** Stop a hold. Safe to call when nothing is held, which is what a pointer leaving does. */
	readonly stop = (): void => {
		if (this.#held !== null) clearTimeout(this.#held);
		this.#held = null;
	};

	/** Move one item that way, then keep moving while held. */
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

	/** Keep the answers in step; call in an $effect and return its result as the cleanup. */
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
