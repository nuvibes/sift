/* A pointer resting on a row is answered by it even while a neighbour's flyout is open: the
 * library's travel triangle lets go only after half a second without moving, which a resting hand
 * never gives. Resting is RESTING_MS on one row without gaining HEADING_PX. Mouse only. */
import { tick } from 'svelte';

/** How long a pointer stays on one row, not closing on the flyout, before that row answers it. */
export const RESTING_MS = 200;

/** How far towards the flyout a pointer has to move to count as still travelling there. */
export const HEADING_PX = 4;

/** Which side of its row a flyout opened on. */
type FlyoutSide = 'left' | 'right';

/** The decision alone: calls `settled` with the row once the pointer rests there. */
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
	close: () => void;
	/** The row that opens it, the library's sub-trigger element. */
	trigger: () => HTMLElement | null;
}

/** Call once in a row that opens out; it listens only while the flyout is open. */
export function givesWayToARestingPointer(row: Resting): void {
	$effect(() => {
		if (!row.open()) return;
		const trigger = row.trigger();
		if (!trigger) return;
		const doc = trigger.ownerDocument;
		let at = { x: 0, y: 0 };

		const watch = new RestingPointer((rested) => {
			row.close();
			/*
			 * After the close settles, the row under the pointer is told, as the library hands it
			 * over.
			 */
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
