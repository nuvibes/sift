import { tick, untrack } from 'svelte';
import type { components } from '$lib/api/schema';
import { imports } from '$lib/library/imports.svelte';
import { durationToken, easingToken, measure, motion } from '$lib/shell/motion.svelte';

/* The bells the whole application rings when something it is drawing has moved. */

/* Whether the rings now are the live feed's rather than a write this tab just made. */
let feedRinging = false;

/** Ring as the live feed does, so a paced re-read walks (`WalkingPace`). */
export function asTheFeed(ring: () => void): void {
	feedRinging = true;
	try {
		ring();
	} finally {
		feedRinging = false;
	}
}

/**
 * How soon a screen's re-read may be asked again for the feed's bells: an import rings every
 * second, and a screen re-reading on each keeps a request in flight most of the time, which
 * the tasks wait behind. The bells within this window of a read ask once, at its end.
 */
export const RE_ASKED_EVERY_MS = 10_000;

/**
 * One re-read at a walking pace. The first bell after a quiet window reads immediately; the bells
 * within `RE_ASKED_EVERY_MS` of the last answer read once, at the window's end, so a change is on
 * screen within the window however fast the bells come. `now` is a write of this tab's own.
 */
export class WalkingPace {
	#read: () => unknown;
	#every: number;
	#last = Number.NEGATIVE_INFINITY;
	#out = 0;
	#owed = false;
	#timer: ReturnType<typeof setTimeout> | null = null;
	#stopped = false;

	constructor(read: () => unknown, every = RE_ASKED_EVERY_MS) {
		this.#read = read;
		this.#every = every;
	}

	/** A bell from the feed: read now, or once at the end of the window. */
	bell(): void {
		if (this.#timer !== null || this.#stopped) return;
		if (this.#out > 0) {
			this.#owed = true;
			return;
		}
		const wait = this.#last + this.#every - Date.now();
		if (wait <= 0) return this.now();
		this.#timer = setTimeout(() => {
			this.#timer = null;
			this.bell();
		}, wait);
	}

	/** Read immediately, covering any bell owed, and start the window from its answer. */
	now(): void {
		this.#clear();
		this.#owed = false;
		this.#last = Date.now();
		const asked = this.#read();
		if (!(asked instanceof Promise)) return;
		this.#out += 1;
		void asked
			.catch(() => undefined)
			.finally(() => {
				this.#out -= 1;
				this.#last = Date.now();
				if (this.#owed && this.#out === 0) {
					this.#owed = false;
					this.bell();
				}
			});
	}

	/** Read nothing more for a bell: the screen has gone. */
	stop(): void {
		this.#stopped = true;
		this.#clear();
	}

	#clear(): void {
		if (this.#timer !== null) clearTimeout(this.#timer);
		this.#timer = null;
	}
}

/** A follower of a bell: a re-read made on every ring, or one at a walking pace. */
type Follower = (() => void) | WalkingPace;

function follow(follower: Follower, own: boolean): void {
	if (typeof follower === 'function') follower();
	else if (own) follower.now();
	else follower.bell();
}

/** One thing that can change, and a number that moves when it does: a count, never the value, so
 * a bell is never a second copy of the state a screen holds. */
class Signal {
	generation = $state(0);

	/** Rises only for a ring of this tab's own write: a paced re-read lets those through. */
	own = 0;

	/* Stores that live as long as the tab (saved searches, the rating scale, the record registry)
	 * have no setup context for an effect, so they get a plain list. Nobody unsubscribes. */
	#lasting = new Set<Follower>();

	changed(): void {
		this.generation += 1;
		if (!feedRinging) this.own += 1;
		for (const listen of this.#lasting) follow(listen, !feedRinging);
	}

	/** Be told for as long as this tab is open. For a store that is not a screen. */
	subscribe(listen: Follower): void {
		this.#lasting.add(listen);
	}
}

/** Re-read when this signal moves, on every ring or at a pace. Call it during component setup. */
export function whenChanged(signal: Signal, reload: Follower): void {
	let seen = untrack(() => signal.generation);
	let own = signal.own;
	$effect(() => {
		const now = signal.generation;
		if (now === seen) return;
		seen = now;
		const mine = signal.own !== own;
		own = signal.own;
		untrack(() => follow(reload, mine));
	});
	if (reload instanceof WalkingPace) $effect(() => () => reload.stop());
}

/** What this account may see has changed, with no new file involved: something shared, hidden,
 * collected, renamed, created or deleted. */
export const libraryChanges = new Signal();

/** Files have entered the library, left it, or moved. */
export const arrivals = new Signal();

/** The work queue moved: something queued, started, finished, failed or got further along. */
export const jobChanges = new Signal();

/** The download queue moved, including a transfer simply getting further along. */
export const downloadChanges = new Signal();

/** A setting moved: this account's own, or one the whole installation shares. */
export const settingChanges = new Signal();

/** A list that is this account's own moved: a saved search, a recent search, or what it thinks of
 * a person, a Site, a collection or a tag. */
export const mine = new Signal();

/** One of this user's screens (an open player or Theater wall offered to their phone) started,
 * stopped, changed what it plays or went away. */
export const screenChanges = new Signal();

/** Which files share a song moved: the music task paired a file. Only the Same music strip hears it. */
export const sameMusicChanges = new Signal();

/** Re-read this screen's list whenever what it may see changes, at a walking pace. */
export function reloadOnLibraryChange(reload: () => void): void {
	const pace = new WalkingPace(reload);
	whenChanged(libraryChanges, pace);
	/* And when this account's own opinions move, which these walls draw; folded in so no call
	 * site forgets. */
	whenChanged(mine, pace);
	reloadOnFilesSettled(pace);
}

/** Re-read once the files on their way in have all landed, so a list's counts follow a scan or a
 * download: one read when the work ends, never one per file, which is what `arrivals` would
 * cost. */
export function reloadOnFilesSettled(reload: Follower): void {
	let seen = untrack(() => imports.settled);
	$effect(() => {
		const now = imports.settled;
		if (now === seen) return;
		seen = now;
		if (untrack(() => imports.busy) === 0) untrack(() => follow(reload, false));
	});
}

/** What one file is to the person looking at it, plus which file: the server's generated shape. */
export type AssetOpinion = components['schemas']['AssetOpinion'];

/** What a screen can move about a file on its own: the heart and the stars. */
export type OpinionPatch = Pick<AssetOpinion, 'favorite' | 'rating'> &
	Partial<Pick<AssetOpinion, 'views' | 'pinned'>>;

type AssetState = AssetOpinion;

/** A heart, a rating or a view count that was just written, so every screen already drawing that
 * file can follow. */
class AssetStateChanges {
	generation = $state(0);
	/** The last one written. A screen reads it when the number moves, and never keeps it. */
	last: AssetState | null = null;

	changed(state: AssetState): void {
		this.last = state;
		this.generation += 1;
	}
}

export const assetState = new AssetStateChanges();

/** Apply somebody else's write to this screen's copy of a row. */
export function onAssetStateChange(apply: (state: AssetState) => void): void {
	let seen = untrack(() => assetState.generation);
	$effect(() => {
		const now = assetState.generation;
		if (now === seen) return;
		seen = now;
		const state = assetState.last;
		if (state) untrack(() => apply(state));
	});
}

/** Something was just written, BY THIS TAB, that a history records. */
export const recorded = new Signal();

/** How long a history waits for the next bell before it asks, so a burst of them is one read. */
export const HISTORY_SETTLE_MS = 250;

/** Re-read a history thread whenever something it records may have moved. */
export function rereadOnHistoryChange(reread: () => void, settle = HISTORY_SETTLE_MS): void {
	let pending: ReturnType<typeof setTimeout> | null = null;
	const soon = () => {
		if (pending !== null) clearTimeout(pending);
		pending = setTimeout(() => {
			pending = null;
			reread();
		}, settle);
	};
	whenChanged(recorded, soon);
	whenChanged(libraryChanges, new WalkingPace(soon));
	// A thread taken off the screen must not ask for itself after it has gone.
	$effect(() => () => {
		if (pending !== null) clearTimeout(pending);
	});
}

/** Each row from where it was drawn to where it is, before the frame is painted: the browser's
 * own animation, which moves what is seen without moving the layout, so nothing reads as a jump. */
function slideFrom(were: Map<Element, DOMRect>): void {
	if (motion.reduced) return;
	const duration = motion.duration(durationToken('--dur-fast', 140));
	const easing = `cubic-bezier(${easingToken('--ease', [0.2, 0, 0, 1]).join(', ')})`;
	for (const [row, was] of were) {
		if (!row.isConnected) continue;
		const now = row.getBoundingClientRect();
		const dx = was.left - now.left;
		const dy = was.top - now.top;
		if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
		row.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }], {
			duration,
			easing
		});
	}
}

/** The box a list scrolls in: the nearest ancestor that scrolls. */
function scrollerOf(element: HTMLElement | null): HTMLElement | null {
	for (let at = element?.parentElement ?? null; at; at = at.parentElement) {
		const overflow = getComputedStyle(at).overflowY;
		if ((overflow === 'auto' || overflow === 'scroll') && at.scrollHeight > at.clientHeight)
			return at;
	}
	return null;
}

/** A list's newcomers, let in immediately while its top is on screen (the rows below slide, see
 * `slideFrom`), and otherwise held back with a quiet "N new" line until the reader scrolls up or
 * presses it, so nothing under somebody reading further down moves. */
export class HeldNewcomers<T extends { id: string }> {
	shown: T[] = $state([]);
	/** How many arrived above and are held: the number the line says. */
	waiting = $state(0);
	#latest: T[] = [];
	#wall: HTMLElement | null = null;

	/** The answer to the question on screen. A new question (a page, an order) is drawn as it is. */
	take(items: T[], fromBell: boolean): void {
		this.#latest = items;
		if (!fromBell || this.shown.length === 0) return this.letIn();
		const drawn = new Set(this.shown.map((row) => row.id));
		const fresh = items.filter((row) => !drawn.has(row.id)).length;
		if (fresh === 0 || this.#atTop()) return this.letIn();
		// The rows the held ones pushed off the page's end stay drawn until they come in.
		const answered = new Set(items.map((row) => row.id));
		const pushedOff: T[] = [];
		for (let at = this.shown.length - 1; at >= 0 && pushedOff.length < fresh; at--) {
			if (answered.has(this.shown[at].id)) break;
			pushedOff.unshift(this.shown[at]);
		}
		this.shown = [...items.filter((row) => drawn.has(row.id)), ...pushedOff];
		this.waiting = fresh;
	}

	letIn(): void {
		const were = this.#wall ? measure(this.#wall.children) : null;
		this.shown = this.#latest;
		this.waiting = 0;
		if (were) void tick().then(() => slideFrom(were));
	}

	/** The element the rows are drawn in; scrolling back to the top lets in what is held. */
	watch(wall: HTMLElement): () => void {
		this.#wall = wall;
		const scroller = scrollerOf(wall);
		const back = () => {
			if (this.waiting > 0 && this.#atTop()) this.letIn();
		};
		scroller?.addEventListener('scroll', back, { passive: true });
		return () => scroller?.removeEventListener('scroll', back);
	}

	/** Whether the first row is on screen: the list scrolled less than half a row. */
	#atTop(): boolean {
		const scroller = scrollerOf(this.#wall);
		const row = this.#wall?.firstElementChild as HTMLElement | null | undefined;
		return !scroller || scroller.scrollTop < (row?.offsetHeight ?? 0) / 2;
	}
}
