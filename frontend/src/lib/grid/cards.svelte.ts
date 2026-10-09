/*
 * Paging a wall of uniform cards, or rows, by whole rows: the count is arithmetic from a MEASURED
 * card, shared by every wall that pages, never a typed-in size.
 */

import { untrack } from 'svelte';

import type { PagerProps } from '$lib/components/common/Pager.svelte';
import type { Anchor, PageAsk } from './anchor';
import { PAGE_SCREENS } from './justify';

/** The kernel's ceiling. */
const SERVER_PAGE_CAP = 200;

interface Measurement {
	areaWidth: number;
	areaHeight: number;
	cardWidth: number;
	cardHeight: number;
	gap: number;
}

/*
 * What each wall measured, kept for the session, keyed by the wall, so a second visit asks at the
 * right size. Never persisted: it would outlive the stylesheet that produced it.
 */
const remembered = new Map<string, Measurement>();

export function forgetMeasurements(): void {
	remembered.clear();
}

export interface Page<T> {
	rows: T[];
	total: number;
	offset: number;
}

export interface Filled<A, T> extends Page<T> {
	answer: A | null;
}

interface Served {
	question: string;
	offset: number;
	size: number;
	count: number;
	total: number;
}

/** A remainder already on its way, which a second resize waits for. */
interface Pending {
	question: string;
	from: number;
	upTo: number;
	answer: Promise<unknown>;
}

/** The AREA decides how many rows to fetch; one CARD says how big a card is. */
export class CardPaging {
	#fallback: number;

	#areaWidth = $state(0);
	#areaHeight = $state(0);

	#cardWidth = $state(0);
	#cardHeight = $state(0);
	#gap = $state(0);

	#everRead = false;

	/** Not a page number. See `Pager`. */
	offset = $state(0);

	/**
	 * The row the FIRST page of a visit starts AT, resolved by the server; cleared on any other
	 * ask.
	 */
	anchor = $state<string | null>(null);

	/** The anchored row's offset, read by the server only when the row is gone (`NEAR`). */
	near: number | null = null;

	#wall: string | null;

	#served: Served | null = null;

	/**
	 * Which page is on screen; a re-read at the same place keeps it, so the entrance does not
	 * replay.
	 */
	showing = $state<string | null>(null);

	#serve(served: Served): void {
		this.#served = served;
		this.showing = `${served.question}\n${served.offset}`;
	}

	#pending: Pending | null = null;

	#ticket = 0;

	#landed: number | null = null;

	constructor(fallback: number, wall: string | null = null) {
		this.#fallback = fallback;
		this.#wall = wall;
		const known = wall === null ? undefined : remembered.get(wall);
		if (known) {
			this.#areaWidth = known.areaWidth;
			this.#areaHeight = known.areaHeight;
			this.#cardWidth = known.cardWidth;
			this.#cardHeight = known.cardHeight;
			this.#gap = known.gap;
			/* A real measurement stands; the attachment's read is a frame old. */
			this.#everRead = true;
		}
	}

	#remember(): void {
		if (this.#wall === null) return;
		if (this.#areaWidth <= 0 || this.#areaHeight <= 0) return;
		if (this.#cardWidth <= 0 || this.#cardHeight <= 0) return;
		remembered.set(this.#wall, {
			areaWidth: this.#areaWidth,
			areaHeight: this.#areaHeight,
			cardWidth: this.#cardWidth,
			cardHeight: this.#cardHeight,
			gap: this.#gap
		});
	}

	/**
	 * The page on screen, asking only for what the rows held do not cover: on a resize alone, cut
	 * to size or ask for the remainder; anything else asks for the whole page. An overtaken answer
	 * is null.
	 */
	async fill<A, T>(
		question: string,
		held: () => readonly T[],
		ask: (query: PageAsk) => Promise<A>,
		read: (answer: A) => Page<T>
	): Promise<Filled<A, T> | null> {
		const ticket = ++this.#ticket;
		const landed = this.#landed;
		this.#landed = null;
		/* UNTRACKED: the rows handed in are what the calling effect writes, or it would loop. */
		const have = untrack(() => held().slice());
		const size = this.size;
		const offset = this.offset;
		const served = this.#served;
		const resized =
			this.anchor === null &&
			served !== null &&
			served.question === question &&
			served.offset === offset &&
			served.count === have.length &&
			served.size !== size;
		const arrived =
			landed === offset &&
			this.anchor === null &&
			served !== null &&
			served.question === question &&
			served.offset === offset &&
			served.count === have.length &&
			served.size === size;
		if (arrived) return { rows: have, total: served.total, offset, answer: null };

		try {
			if (resized) {
				const topped = await this.#topUp(
					{ question, have, size, offset, total: served.total, ticket },
					ask,
					read
				);
				if (topped !== undefined) return topped;
			}

			const answer = await ask(this.query);
			if (ticket !== this.#ticket) return null;
			const within = await this.#within({ page: read(answer), answer }, size, ticket, ask, read);
			if (within === null) return null;
			const page = within.page;
			this.#serve({
				question,
				offset: page.offset,
				size,
				count: page.rows.length,
				total: page.total
			});
			return { ...page, answer: within.answer };
		} catch (error) {
			if (ticket !== this.#ticket) return null;
			this.#pending = null;
			this.#served = null;
			throw error;
		}
	}

	async #topUp<A, T>(
		now: {
			question: string;
			have: readonly T[];
			size: number;
			offset: number;
			total: number;
			ticket: number;
		},
		ask: (query: PageAsk) => Promise<A>,
		read: (answer: A) => Page<T>
	): Promise<Filled<A, T> | null | undefined> {
		const { question, have, size, offset, total, ticket } = now;
		if (size <= have.length || offset + have.length >= total) {
			const rows = have.slice(0, size);
			this.#serve({ question, offset, size, count: rows.length, total });
			return { rows, total, offset, answer: null };
		}
		const from = offset + have.length;
		const upTo = offset + size;
		let pending = this.#pending;
		if (
			pending === null ||
			pending.question !== question ||
			pending.from !== from ||
			pending.upTo < upTo
		) {
			pending = { question, from, upTo, answer: ask({ limit: upTo - from, offset: from }) };
			this.#pending = pending;
		}
		const answer = (await pending.answer) as A;
		if (ticket !== this.#ticket) return null;
		this.#pending = null;
		const more = read(answer);
		// The list moved under the held rows, so the page is read whole.
		if (more.offset === from) {
			const rows = [...have, ...more.rows].slice(0, size);
			this.#serve({ question, offset, size, count: rows.length, total: more.total });
			return { rows, total: more.total, offset, answer };
		}
		return undefined;
	}

	/**
	 * A page past the end of the list, stepped back to the new last page: answering the last card
	 * on the last page empties it. Each step moves nearer the front, so it cannot loop.
	 */
	async #within<A, T>(
		first: { page: Page<T>; answer: A },
		size: number,
		ticket: number,
		ask: (query: PageAsk) => Promise<A>,
		read: (answer: A) => Page<T>
	): Promise<{ page: Page<T>; answer: A } | null> {
		let at = first;
		while (at.page.rows.length === 0 && at.page.offset > 0) {
			const past = at.page.offset;
			let total = at.page.total;
			/*
			 * A ZERO HERE IS NOT A COUNT: a windowed count over no rows; the front's count is real.
			 */
			if (total <= 0) {
				const front = await ask({ limit: size, offset: 0 });
				if (ticket !== this.#ticket) return null;
				at = { page: read(front), answer: front };
				total = at.page.total;
				if (total <= size) return at;
			}
			const back = Math.floor((total - 1) / size) * size;
			if (back >= past) break;
			const answer = await ask({ limit: size, offset: back });
			if (ticket !== this.#ticket) return null;
			at = { page: read(answer), answer };
		}
		return at;
	}

	/** `from` on arrival, `offset` ever after, never both. */
	get query(): PageAsk {
		if (this.anchor === null) return { limit: this.size, offset: this.offset };
		return this.near === null
			? { limit: this.size, from: this.anchor }
			: { limit: this.size, from: this.anchor, near: this.near };
	}

	/**
	 * Say where the page just drawn begins, so the next `fill` answers from the rows held rather
	 * than asking again. Call it in the SAME synchronous turn as writing the rows.
	 */
	land(at: number): void {
		this.anchor = null;
		this.near = null;
		if (at === this.offset) return;
		this.#landed = at;
		this.offset = at;
	}

	arrive(anchor: Anchor | null): void {
		this.anchor = anchor?.from ?? null;
		this.near = anchor?.near ?? null;
		this.#landed = null;
		this.offset = 0;
	}

	/** Called when the question changes, or the old anchor would apply to the new one. */
	forget(): void {
		this.anchor = null;
		this.near = null;
	}

	/** A different list starts at its top. True when the offset moved. */
	restart(): boolean {
		this.anchor = null;
		this.near = null;
		this.#landed = null;
		if (this.offset === 0) return false;
		this.offset = 0;
		return true;
	}

	/** Whole rows times `PAGE_SCREENS`; the fallback until measured, never zero. */
	readonly size = $derived.by(() => {
		if (this.#areaWidth <= 0 || this.#areaHeight <= 0) return this.#fallback;
		if (this.#cardWidth <= 0 || this.#cardHeight <= 0) return this.#fallback;

		// `auto-fill` costs a card plus one gap, one given back, as in `justify.ts`.
		const perRow = Math.max(
			1,
			Math.floor((this.#areaWidth + this.#gap) / (this.#cardWidth + this.#gap))
		);
		const rows = Math.max(
			1,
			Math.floor((this.#areaHeight + this.#gap) / (this.#cardHeight + this.#gap))
		);

		return Math.min(SERVER_PAGE_CAP, perRow * rows * PAGE_SCREENS);
	});

	/** Attach to the element holding the cards; it finds its scrolling box, observed for size. */
	cards = (element: HTMLElement): (() => void) => {
		const area = scrollParent(element);

		const read = () => {
			const style = getComputedStyle(element);
			// `rowGap` is `normal` on a non-grid container, which parses to NaN.
			const rowGap = parseFloat(style.rowGap);
			this.#gap = Number.isFinite(rowGap) ? rowGap : 0;

			/*
			 * ZERO IS NOT A MEASUREMENT: a wall unmounted while it loads reports nothing, and
			 * taking it would refetch in a loop.
			 */
			const box = area.getBoundingClientRect();
			if (area.clientWidth > 0) this.#areaWidth = area.clientWidth;
			if (box.height > 0) this.#areaHeight = box.height;

			const card = element.firstElementChild;
			if (!(card instanceof HTMLElement)) return;
			const shape = card.getBoundingClientRect();
			if (shape.width > 0) this.#cardWidth = shape.width;
			if (shape.height > 0) this.#cardHeight = shape.height;
			this.#remember();
		};

		/*
		 * The synchronous read happens ONCE, ever: on a remount it measures the frame without the
		 * wall and comes out short, and the page size would oscillate. jsdom's observer never
		 * delivers.
		 */
		if (!this.#everRead) {
			this.#everRead = true;
			read();
		}
		if (typeof ResizeObserver === 'undefined') return () => {};
		const observer = new ResizeObserver(read);
		observer.observe(element);
		observer.observe(area);
		return () => observer.disconnect();
	};

	goTo(offset: number, total: number): void {
		// Turning a page stops the address's anchor applying.
		this.anchor = null;
		this.near = null;
		this.#landed = null;
		this.offset = Math.max(0, Math.min(offset, Math.max(0, total - 1)));
	}

	step(direction: 1 | -1, total: number): void {
		this.goTo(this.offset + direction * this.size, total);
	}

	/** The last whole multiple of the page size, so pages tile from the front. */
	last(total: number): void {
		this.anchor = null;
		this.near = null;
		this.#landed = null;
		this.offset = total <= 0 ? 0 : Math.floor((total - 1) / this.size) * this.size;
	}

	asPager(shown: number, total: number, noun: string): PagerProps {
		return {
			offset: this.offset,
			shown,
			total,
			noun,
			onfirst: () => this.goTo(0, total),
			onprevious: () => this.step(-1, total),
			onnext: () => this.step(1, total),
			onlast: () => this.last(total),
			onjump: (position) => this.goTo(position - 1, total)
		};
	}
}

/** Walks up to the first scrolling box; a wall does not always own its scroller. */
export function scrollParent(element: HTMLElement): HTMLElement {
	let at = element.parentElement;
	while (at) {
		const overflow = getComputedStyle(at).overflowY;
		if (overflow === 'auto' || overflow === 'scroll') return at;
		at = at.parentElement;
	}
	return document.documentElement;
}

export interface HeldWall<T> {
	items: T[];
	total: number;
	at: number;
	loaded: boolean;
	loading: boolean;
	failed: boolean;
}

/**
 * A store's page through `CardPaging.fill`, the rows written and the offset landed in one turn.
 * `current` turns false once the store has moved on.
 */
export async function fillHeld<A, T>(
	store: HeldWall<T>,
	paging: CardPaging,
	question: string,
	ask: (query: PageAsk) => Promise<A>,
	read: (answer: A) => Page<T>,
	current: () => boolean
): Promise<void> {
	store.failed = false;
	try {
		/* `loading` only when a request goes out. */
		const asking = (query: PageAsk) => {
			store.loading = true;
			return ask(query);
		};
		const page = await paging.fill(question, () => store.items, asking, read);
		if (page === null || !current()) return;
		store.items = page.rows;
		store.total = page.total;
		store.at = page.offset;
		store.loaded = true;
		paging.land(page.offset);
	} catch {
		if (!current()) return;
		store.failed = true;
	} finally {
		if (current()) store.loading = false;
	}
}
