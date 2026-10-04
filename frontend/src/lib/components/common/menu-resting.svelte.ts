/*
 * A pointer that has come to rest on a row is answered by that row, even while a neighbour's
 * flyout is open.
 *
 * The library keeps a flyout open while the pointer travels towards it: a row that opens out
 * draws a triangle from the pointer to the flyout's near edge, and while the pointer is inside
 * that triangle no other row of the menu answers it, so a diagonal from "Rating" to its stars does
 * not open whatever the diagonal crosses on the way. It lets go when the pointer leaves the
 * triangle, or when the pointer has not moved for half a second.
 *
 * That second rule is the fault. A hand resting on a mouse is never perfectly still: the pointer
 * moves a pixel now and then, every one of those moves restarts the library's half second, and
 * a flyout that is tall (the rating's six rows, a list of people) throws a triangle over the rows
 * beside the one that opened it. So a pointer resting on "Add to" while the rating's flyout is
 * open would never open Add to's, and somebody would have to wiggle the pointer out of the
 * triangle to get the row they were already on.
 *
 * So each row that opens out also asks one question of its own while its flyout is open: has the
 * pointer come to rest on another row of the same menu? Resting means on the same row for
 * `RESTING_MS` without closing on the flyout by `HEADING_PX` (a pointer still on its way keeps
 * gaining ground towards the flyout; a resting hand's tremor gains none). When it has, the flyout
 * closes and the row under the pointer is handed the pointer, exactly as the library hands it one
 * when its own triangle lets go, so it opens or lights as if the pointer had just arrived. A pass
 * across a row on the way to the flyout takes well under `RESTING_MS` and gains ground all the
 * way, so the triangle keeps doing its job.
 *
 * Mouse only. A touch screen draws every flyout as a sheet over the menu, so there is no
 * neighbouring row to rest on, and a pen hovers so rarely that the library's rule serves it.
 */
import { tick } from 'svelte';

/** How long a pointer stays on one row, not closing on the flyout, before that row answers it. */
export const RESTING_MS = 200;

/** How far towards the flyout a pointer has to move to count as still travelling there. */
export const HEADING_PX = 4;

/** Which side of its row a flyout opened on. */
type FlyoutSide = 'left' | 'right';

/**
 * The decision alone, with no document: told where the pointer is, it calls `settled` with the
 * row once the pointer has rested there.
 */
export class RestingPointer {
	#row: Element | null = null;
	#anchor = 0;
	#timer: ReturnType<typeof setTimeout> | null = null;
	readonly #settled: (row: Element) => void;
	readonly #ms: number;

	constructor(settled: (row: Element) => void, ms: number = RESTING_MS) {
		this.#settled = settled;
		this.#ms = ms;
	}

	/** The pointer is over `row`, another row of the menu, at `x`, with the flyout on `side`. */
	over(row: Element, x: number, side: FlyoutSide): void {
		const gained = side === 'right' ? x - this.#anchor : this.#anchor - x;
		if (row === this.#row && this.#timer !== null && gained < HEADING_PX) return;
		this.#row = row;
		this.#anchor = x;
		this.#start();
	}

	/** The pointer is not on another row: on the flyout, on the row that opened it, or off the menu. */
	away(): void {
		this.#stop();
		this.#row = null;
	}

	#start(): void {
		this.#stop();
		this.#timer = setTimeout(() => {
			this.#timer = null;
			const row = this.#row;
			this.#row = null;
			if (row) this.#settled(row);
		}, this.#ms);
	}

	#stop(): void {
		if (this.#timer !== null) clearTimeout(this.#timer);
		this.#timer = null;
	}
}

/** Another row of the menu `trigger` sits in, under the pointer, or null. */
export function neighbourRow(trigger: Element, target: EventTarget | null): Element | null {
	if (!(target instanceof Element)) return null;
	const row = target.closest('[role^="menuitem"]');
	if (!row || row === trigger) return null;
	const menu = trigger.closest('[role="menu"]');
	return menu !== null && row.closest('[role="menu"]') === menu ? row : null;
}

/** Which side of `trigger` its open flyout is on: the one the library names in `aria-controls`. */
function flyoutSide(trigger: Element): FlyoutSide {
	const id = trigger.getAttribute('aria-controls');
	const flyout = id ? trigger.ownerDocument.getElementById(id) : null;
	if (!flyout) return 'right';
	const from = trigger.getBoundingClientRect();
	const to = flyout.getBoundingClientRect();
	return to.left + to.width / 2 >= from.left + from.width / 2 ? 'right' : 'left';
}

export interface Resting {
	/** Whether this row's flyout is open. */
	open: () => boolean;
	/** Close it. */
	close: () => void;
	/** The row that opens it, the library's sub-trigger element. */
	trigger: () => HTMLElement | null;
}

/**
 * Call once in a component that draws a row opening out (`ContextMenuItem`, `PickMenu`). The
 * listener lives only while the flyout is open.
 */
export function givesWayToARestingPointer(row: Resting): void {
	$effect(() => {
		if (!row.open()) return;
		const trigger = row.trigger();
		if (!trigger) return;
		const doc = trigger.ownerDocument;
		let at = { x: 0, y: 0 };

		const watch = new RestingPointer((rested) => {
			row.close();
			/* After the close has settled, so the library has let go of the pointer; then the row
			   under it is told where it is, which is the library's own hand-over. */
			void tick().then(() =>
				rested.dispatchEvent(
					new PointerEvent('pointermove', {
						bubbles: true,
						cancelable: true,
						pointerType: 'mouse',
						clientX: at.x,
						clientY: at.y
					})
				)
			);
		});

		const moved = (event: PointerEvent): void => {
			if (event.pointerType !== 'mouse') return;
			at = { x: event.clientX, y: event.clientY };
			const other = neighbourRow(trigger, event.target);
			if (other) watch.over(other, event.clientX, flyoutSide(trigger));
			else watch.away();
		};

		doc.addEventListener('pointermove', moved, true);
		return () => {
			doc.removeEventListener('pointermove', moved, true);
			watch.away();
		};
	});
}
