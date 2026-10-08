/*
 * Which mark of a chart is pointed at, by the pointer or the keyboard.
 *
 * A chart is one tab stop however many marks it has. The arrows move along its marks, Escape lets
 * go, and the pointed mark's tooltip is held while the chart has focus, so a keyboard reads the
 * same bubble a pointer does.
 */

/** How far each arrow moves: a bar chart and the ring go one mark either way, a calendar a week sideways. */
export interface Steps {
	ArrowLeft: number;
	ArrowRight: number;
	ArrowUp: number;
	ArrowDown: number;
}

const ALONG: Steps = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -1, ArrowDown: 1 };

export class Pointing {
	at = $state<number | null>(null);
	focused = $state(false);
	#count: () => number;
	#steps: Steps;

	constructor(count: () => number, steps: Steps = ALONG) {
		this.#count = count;
		this.#steps = steps;
	}

	/** Whether this mark's tooltip is held up by the keyboard. */
	held(index: number): boolean {
		return this.focused && this.at === index;
	}

	point = (index: number | null) => {
		this.at = index;
	};

	/* Arriving starts at the first mark, so the bubble says where the arrows begin. */
	focus = () => {
		this.focused = true;
		if (this.at === null && this.#count() > 0) this.at = 0;
	};

	blur = () => {
		this.focused = false;
		this.at = null;
	};

	keydown = (event: KeyboardEvent) => {
		const count = this.#count();
		if (count === 0) return;
		if (event.key === 'Escape') {
			if (this.at === null) return;
			event.stopPropagation();
			this.at = null;
			return;
		}
		let next: number;
		if (event.key === 'Home') next = 0;
		else if (event.key === 'End') next = count - 1;
		else if (event.key in this.#steps) {
			const step = this.#steps[event.key as keyof Steps];
			next = this.at === null ? (step > 0 ? 0 : count - 1) : this.at + step;
		} else return;
		event.preventDefault();
		this.at = Math.max(0, Math.min(count - 1, next));
	};
}
