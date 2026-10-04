/*
 * Paging a wall of uniform cards, or a list of uniform rows, by whole rows.
 *
 * The media grid gets this the hard way: its tiles are all different shapes, so how many fill a row
 * cannot be known until they are laid out, and the page has to be fetched generously and trimmed
 * (see `justify.ts` and `Grid.loadAt`). Everything else in Sift that pages is uniform (a wall of
 * faces, of people, of suggestions), and for those the count is plain arithmetic done in advance.
 *
 * It is here rather than in each of the six screens for the reason everything shared is: six copies
 * of "how many cards fit" is six chances for one of them to be off by a row, and the symptom is a
 * strip of dead space or a row cut in half at the bottom of one screen and not the others. Exactly
 * the sort of difference nobody reports and everybody feels.
 *
 * A FIXED count (24, 60, 50) is the same on an 11-inch laptop as on a 32-inch monitor: on the
 * laptop it overflows and scrolls; on the monitor it leaves half the screen empty.
 *
 * **Nothing here is a declared card size.** The card is MEASURED, off a real one on screen, because
 * a number typed in beside a component is a number that stops being true the first time anybody
 * changes its padding, and nothing would say so. No magic numbers.
 */

import { untrack } from 'svelte';

import type { PagerProps } from '$lib/components/common/Pager.svelte';
import type { Anchor, PageAsk } from './anchor';
import { PAGE_SCREENS } from './justify';

/** The most rows any of these endpoints will hand back at once. The kernel's ceiling. */
const SERVER_PAGE_CAP = 200;

/** Everything a page size is worked out from, as a real wall last reported it. */
interface Measurement {
	areaWidth: number;
	areaHeight: number;
	cardWidth: number;
	cardHeight: number;
	gap: number;
}

/*
 * WHAT EACH WALL MEASURED, KEPT FOR THE REST OF THE SESSION.
 *
 * A wall cannot measure a card until one is drawn, so its first request goes out at the fallback
 * count, and the moment the first card draws the measured size differs and the whole first page
 * would be asked for again, on every visit, because a wall is a new component each time.
 *
 * The card does not change size between two visits to the same wall, so the second visit asks at
 * the measured size straight away. Keyed by the WALL (a name the screen gives), not by the thing it
 * shows: every person's faces screen draws the same card.
 *
 * Module state on purpose: a session's worth, gone on a reload, which is also when a changed
 * stylesheet would arrive. Never persisted: a card size written to storage would outlive the
 * stylesheet that produced it, and nothing would say so.
 */
const remembered = new Map<string, Measurement>();

/** For tests: forget every wall's measurement, as a reload would. */
export function forgetMeasurements(): void {
	remembered.clear();
}

/** One page of a wall, as the server answered it: the rows, the whole list's length, where it began. */
export interface Page<T> {
	rows: T[];
	total: number;
	offset: number;
}

/** What `fill` hands back: the page to draw, and the server's answer when one was asked for. */
export interface Filled<A, T> extends Page<T> {
	/** Null when the rows already held covered the new size and nothing was asked. */
	answer: A | null;
}

/** What the last page `fill` settled was: which question, where, at what size, how many rows. */
interface Served {
	question: string;
	offset: number;
	size: number;
	count: number;
	total: number;
}

/** A remainder already on its way, so a second resize inside it waits for it rather than asking. */
interface Pending {
	question: string;
	from: number;
	upTo: number;
	answer: Promise<unknown>;
}

/**
 * A wall's paging: how big a page is here, and where the one on screen starts.
 *
 * Two things get measured and they are different elements. The AREA is the box the cards scroll
 * inside, which is what decides how many rows are worth fetching. The CARD is one of the cards, and
 * only it knows how big a card is.
 */
export class CardPaging {
	/** What to ask for before anything has been measured: the screen's own fixed count. */
	#fallback: number;

	/** The box the cards live in. */
	#areaWidth = $state(0);
	#areaHeight = $state(0);

	/** One card, measured. Zero until the first page has drawn something. */
	#cardWidth = $state(0);
	#cardHeight = $state(0);
	#gap = $state(0);

	/** Whether a box has ever been measured. See the attachment for what this is guarding. */
	#everRead = false;

	/** Where the page on screen begins, counting from zero. Not a page number. See `Pager`. */
	offset = $state(0);

	/**
	 * The row the page should start AT, when the address named one, instead of the offset.
	 *
	 * Set once on arrival and cleared the moment anything is turned or asked differently. See
	 * `arrive` and `forget`. It is not a second way of saying where the page is: `offset` is still
	 * the truth, and this is only how the FIRST page of a visit is found.
	 *
	 * The server resolves it, not this. Only the server knows the scoped, ordered list the anchor is
	 * a position in, and working it out here from a page already in hand would be a position in the
	 * page rather than in the wall.
	 */
	anchor = $state<string | null>(null);

	/**
	 * The offset the anchored row was at when the address was written, sent beside it.
	 *
	 * Only the server reads it, and only when the row is gone. See `NEAR` in `./anchor`. Held and
	 * cleared with `anchor`, never on its own: without a row it would be a page number, which is the
	 * thing the address deliberately does not carry.
	 */
	near: number | null = null;

	/** The wall's name, for the session's memory of what it measured. Null keeps no memory. */
	#wall: string | null;

	/** The last page `fill` settled, which a resize is measured against. */
	#served: Served | null = null;

	/**
	 * Which page is on screen, as the question and the place it was asked for. Null before the first.
	 *
	 * What a wall's entrance is keyed on. Re-reading the same question at the same place (a library
	 * change, a task getting further along) leaves it as it was, so a count that moved does not
	 * replay the page's arrival; a page turned, a new filter or a new order changes it.
	 */
	showing = $state<string | null>(null);

	#serve(served: Served): void {
		this.#served = served;
		this.showing = `${served.question}\n${served.offset}`;
	}

	/** A remainder in flight. */
	#pending: Pending | null = null;

	/** Bumped by every `fill`, so an answer that a newer one has overtaken is recognised. */
	#ticket = 0;

	/** Where `land` moved the offset to, until the next `fill` has read it. See `land`. */
	#landed: number | null = null;

	/**
	 * @param fallback What to ask for before this wall has ever been measured.
	 * @param wall A name for this wall, the same on every visit. With one, what the wall measured is
	 *   remembered for the session and the next visit's first request is already the right size.
	 */
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
			/* A real measurement already stands, so the attachment's synchronous read, which
			   measures the box as it was a frame ago, must not overwrite it. The observer's
			   delivery, after layout, is what corrects it if the window has changed since. */
			this.#everRead = true;
		}
	}

	/** Write the current measurement down for this wall's next visit, once it is a whole one. */
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
	 * Read the page on screen, asking the server only for what the rows already held do not cover.
	 *
	 * A wall that is re-sized (including the first measurement of a visit, which is a re-size
	 * from the fallback) already holds the rows at the top of the new page, and asking for the
	 * whole page again would be a second request for rows on screen. So when the question and the
	 * place are the ones last served and only the SIZE moved:
	 *
	 * - smaller: the rows held are cut to the new size, and nothing is asked;
	 * - larger: only the REMAINDER is asked for, as a page starting where the held rows end,
	 *   or nothing, when the held rows are already the whole rest of the list.
	 *
	 * Anything else asks for the whole page: another question, another place, the same size
	 * again (a re-read after a verb, which must see what the verb changed), an anchor from the
	 * address, or rows that were edited on screen since they were served.
	 *
	 * Answers that a newer `fill` has overtaken come back as null and are not to be drawn: two
	 * reads in flight land in whatever order the network chooses, and the older one must not paint
	 * over the newer. A failure of an overtaken read is also null, not a fault on screen.
	 *
	 * @param question What the wall is showing: the person, the tab, the status. Rows served
	 *   for one question are never trimmed or extended to answer another.
	 * @param held The rows on screen now, as a getter: it is read untracked (see below), and an
	 *   argument read at the call would already have been tracked by the caller's effect.
	 * @param ask One request, with the query to send.
	 * @param read The server's answer as a page.
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
		/* UNTRACKED, and it is the difference between a wall and a loop. Every wall calls this from
		   the effect that watches the page's size and place, and the rows it hands in are the state
		   that effect then writes. Read plainly, the effect would depend on its own answer and read
		   again every time one landed, for ever. */
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
		/* The re-run that `land` itself caused: the rows held ARE this page, just served. */
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
				if (size <= have.length || offset + have.length >= served.total) {
					const rows = have.slice(0, size);
					this.#serve({ question, offset, size, count: rows.length, total: served.total });
					return { rows, total: served.total, offset, answer: null };
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
				// The list moved under the held rows (the server began somewhere else): what is
				// held no longer lines up with what follows it, so read the page whole instead.
				if (more.offset === from) {
					const rows = [...have, ...more.rows].slice(0, size);
					this.#serve({ question, offset, size, count: rows.length, total: more.total });
					return { rows, total: more.total, offset, answer };
				}
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

	/**
	 * A page that begins past the end of the list, stepped back to where the list now ends.
	 *
	 * ANSWERING THE LAST CARD ON THE LAST PAGE EMPTIES THAT PAGE, and the rest of the list is still
	 * there. Every queue wall re-reads at the same offset after a verb, the server answers no rows
	 * with a total that does not reach it, and the wall would draw "No ... waiting" over a count
	 * that says otherwise. It is here so it is one rule for every wall: the re-read lands on the
	 * new last page (the same page `last` goes to, so the pages still tile from the front), and
	 * on the top when nothing is left. The wall then `land`s at the offset this answers and writes
	 * its anchor from the first row, as it always does.
	 *
	 * Asked again rather than kept empty: the page to show is one the wall does not hold. Each step
	 * moves strictly nearer the front, so a list shrinking again under the second read cannot loop.
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
			/* A ZERO HERE IS NOT A COUNT. Most walls count with a window over the page's own rows
			   (`COUNT(*) OVER ()`), and a page past the end has no rows to count over, so it says 0
			   for a list that still holds cards. Read as empty, the wall would draw "No ...
			   waiting" over a list with cards in it, after answering the last card on a last page,
			   and after coming back to a page whose anchor was gone and whose `near` now sits past
			   the end. The front of the list is never past the end, so its count is real. */
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

	/**
	 * What to send with the next request: `from` on arrival, `offset` ever after.
	 *
	 * Both would be wrong together. A request carrying an anchor AND an offset asks two questions,
	 * and which one the server honours is then a detail of the server rather than a decision.
	 */
	get query(): PageAsk {
		if (this.anchor === null) return { limit: this.size, offset: this.offset };
		return this.near === null
			? { limit: this.size, from: this.anchor }
			: { limit: this.size, from: this.anchor, near: this.near };
	}

	/**
	 * Say where the page just drawn actually begins, once its rows are on screen.
	 *
	 * An anchored page is asked for by a ROW, and only the answer says what offset that row is at,
	 * so the offset moves after the answer lands, and every wall watches the offset. Moved
	 * plainly (`paging.offset = at`), the watching effect would run again, find the same question
	 * at the same size, take it for a re-read after a verb, and ask for the page it had just been
	 * handed.
	 *
	 * Moved through here, the next `fill` knows the rows it holds were served for exactly this
	 * place and answers from them without asking. Only the NEXT fill: a verb's re-read after that
	 * is a real question again. And only when the offset actually moved: when it did not, nothing
	 * re-runs, and a mark left standing would be read by the next verb's re-read and serve it stale
	 * rows.
	 *
	 * !! Call it in the SAME synchronous turn as writing the rows. The effect that watches the
	 * offset runs on the next microtask; a wall that wrote the rows after an `await` would re-run
	 * with the old rows in hand, and the count would not match. The same ordering keeps the place
	 * on a cold `?from=` link: otherwise the rows land, the first card measures, and the re-run
	 * fires before the offset has moved, asking for offset 0.
	 */
	land(at: number): void {
		this.anchor = null;
		this.near = null;
		if (at === this.offset) return;
		this.#landed = at;
		this.offset = at;
	}

	/** Honour an anchor from the address. Ignored once anybody has turned a page or asked anew. */
	arrive(anchor: Anchor | null): void {
		this.anchor = anchor?.from ?? null;
		this.near = anchor?.near ?? null;
		this.#landed = null;
		this.offset = 0;
	}

	/**
	 * Stop honouring the anchor.
	 *
	 * Called when the question changes: a new search, a different order, a page turned. Without
	 * it the anchor written for the LAST question would be honoured on the next one, and typing a
	 * search would land somebody forty rows into it with nothing saying why. The media grid holds
	 * the same rule.
	 */
	forget(): void {
		this.anchor = null;
		this.near = null;
	}

	/**
	 * Start a different question at its top.
	 *
	 * For a list whose filter moved (a kind chosen, a state switched, "only the ones that need
	 * you"), which is a different list, so the anchor written for the last one stops applying and
	 * the page goes back to the first. `forget` alone would keep the offset, opening the new list
	 * on whatever page the old one had been turned to.
	 *
	 * True when the offset moved, which is what reads the new page on a list whose effect watches
	 * it; false when it was already at the top, where the caller reads it itself. One rule for
	 * every list in Organize with a filter.
	 */
	restart(): boolean {
		this.anchor = null;
		this.near = null;
		this.#landed = null;
		if (this.offset === 0) return false;
		this.offset = 0;
		return true;
	}

	/**
	 * How many cards fill this page.
	 *
	 * Whole rows only, times `PAGE_SCREENS`, so the page is the same shape as every other paged
	 * view in the app: a small whole number of screenfuls, with the pager underneath and outside
	 * them.
	 *
	 * A wall that has not been measured yet asks for its fallback count rather than for nothing. A
	 * first request of zero rows is an empty screen that fills in a moment later, which reads as
	 * the page having failed and then changed its mind.
	 */
	readonly size = $derived.by(() => {
		if (this.#areaWidth <= 0 || this.#areaHeight <= 0) return this.#fallback;
		if (this.#cardWidth <= 0 || this.#cardHeight <= 0) return this.#fallback;

		// `auto-fill` puts in as many tracks as fit at the minimum, gaps included, so a card costs
		// its own width plus one gap, and one gap is given back at the end. The row maths in
		// `justify.ts` counts the same way, for the same reason.
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

	/**
	 * Attach to the element holding the cards. It measures everything from there.
	 *
	 * One attachment rather than two, and it finds the scrolling box itself. The alternative would be
	 * for every screen to hand over both its card container and whatever scrolls around it, which is a
	 * thing to remember on six screens and a thing to get wrong on one of them, and the wall does
	 * not always own its own scroller anyway, so it could not always have named it.
	 *
	 * A ResizeObserver rather than a window listener: both boxes change without the window doing,
	 * every time the rail collapses at a breakpoint or a scrollbar appears, and a page sized to a
	 * width that has since changed by fifteen pixels is a page with a hole in the last row.
	 */
	cards = (element: HTMLElement): (() => void) => {
		const area = scrollParent(element);

		const read = () => {
			const style = getComputedStyle(element);
			// A grid's two gaps are usually the same, and the row one is what a page's height is
			// counted in. `rowGap` is `normal` on a non-grid container, which parses to NaN.
			const rowGap = parseFloat(style.rowGap);
			this.#gap = Number.isFinite(rowGap) ? rowGap : 0;

			/*
			 * ZERO IS NOT A MEASUREMENT. It is the absence of one, and writing it down starts a
			 * loop that never stops.
			 *
			 * Every screen using this replaces its wall while it is loading: "Looking...", a
			 * skeleton, a spinner. So the observed element is REMOVED for the length of each fetch,
			 * and the observer duly reports a box of nothing. Written down, that would make `size`
			 * fall back to the unmeasured default, which is a DIFFERENT number from the real size,
			 * so the effect watching `size` would see it change and fetch again, which unmounts
			 * the wall again: dozens of requests a second, for as long as the tab was open, with
			 * nothing failing and the screen looking fine.
			 *
			 * The card and the area are both guarded this way: the last real measurement stands
			 * until there is another real one.
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
		 * The synchronous read happens ONCE, ever, and never again on a remount.
		 *
		 * An attachment runs the moment its element is in the DOM and BEFORE the browser has laid
		 * the page out again, so a box measured there is the box as it was a frame ago. Every
		 * screen using this replaces its wall while it loads ("Looking...", a skeleton), so the
		 * frame it measures is the one WITHOUT the wall in it, and the area comes out 48px short.
		 * Less than a card, and enough to cross a whole-row boundary: four rows become three, the
		 * page size falls from 80 to 60, the effect watching the size fetches again, the wall comes
		 * down for the fetch, and the next attachment measures the short box again: a screen that
		 * never leaves "Looking...", with nothing failing.
		 *
		 * On the FIRST attachment there is nothing else to go on and a stale box beats no box, so
		 * the read stands, and it is also the only reading the tests can get, since jsdom's
		 * observer never delivers anything. On every attachment after that a real measurement
		 * already exists, and the observer's own delivery (which arrives AFTER layout) is the
		 * only thing allowed to change it.
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

	/** Move to the page that begins here, clamped to the list. */
	goTo(offset: number, total: number): void {
		// Turning a page is asking a new question about where we are, so the anchor from the address
		// stops applying. Left set, every press would re-serve the page the link pointed at.
		this.anchor = null;
		this.near = null;
		this.#landed = null;
		this.offset = Math.max(0, Math.min(offset, Math.max(0, total - 1)));
	}

	/** Forward or back one page. */
	step(direction: 1 | -1, total: number): void {
		this.goTo(this.offset + direction * this.size, total);
	}

	/**
	 * The last page.
	 *
	 * The last whole multiple of the page size, so the pages tile the list from the front. Landing
	 * the final card exactly at the bottom instead would move every page boundary each time the list
	 * grew, and the page somebody was on would shift under them.
	 */
	last(total: number): void {
		this.anchor = null;
		this.near = null;
		this.#landed = null;
		this.offset = total <= 0 ? 0 : Math.floor((total - 1) / this.size) * this.size;
	}

	/**
	 * This paging as the pager's own props, for a screen handing its pager up to the frame.
	 *
	 * Written once here rather than as nine handlers in every wall that pages by cards: a copy of
	 * the same five lines in each wall is a copy free to drift.
	 */
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

/**
 * The box this element actually scrolls inside.
 *
 * Walks up until something has a scrolling overflow, and falls back to the document element, which
 * is the honest answer for a page that scrolls as a whole rather than in a box.
 *
 * Worth doing rather than asking each screen to name its scroller: several of these walls are drawn
 * INSIDE something else (a queue, a person's page) and do not own the box they scroll in, so they
 * could not name it even if asked.
 */
export function scrollParent(element: HTMLElement): HTMLElement {
	let at = element.parentElement;
	while (at) {
		const overflow = getComputedStyle(at).overflowY;
		if (overflow === 'auto' || overflow === 'scroll') return at;
		at = at.parentElement;
	}
	return document.documentElement;
}

/** What a list store holding one wall's page carries: the People, Sites, Tags and Collections stores. */
export interface HeldWall<T> {
	items: T[];
	total: number;
	/** Where the page on screen begins, as the server resolved it. */
	at: number;
	loaded: boolean;
	loading: boolean;
	failed: boolean;
}

/**
 * Fill a STORE's page through a wall's paging: `CardPaging.fill`, then the rows written and the
 * offset landed in one synchronous turn.
 *
 * The walls outside Organize keep their page in a store, because other screens read the same list.
 * Routed through here they share one copy of the loading/failed/overtaken bookkeeping: the first
 * visit trims or asks for the remainder instead of asking twice, and an anchored page lands without
 * being asked again. Writing the rows and landing the offset together is the ordering `land`
 * requires; done by each caller after an `await`, it would lose a cold link's place.
 *
 * @param current False once the store has moved on: a newer read, or an edit made in place that
 * an
 *   older answer must not overwrite. The store's own generation counter, read after the answer.
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
		/* `loading` only when a request actually goes out: a landing or a trim answers from the rows
		   held and asks nothing, and saying "Looking..." for it would flash the wall away for a frame. */
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
