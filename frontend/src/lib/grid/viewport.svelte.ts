/*
 * One observer for the whole grid, and a capped pool of video elements shared between its tiles.
 *
 * Both exist for the same reason, and it is the reason a grid of a few hundred items feels
 * different from a grid of a few thousand.
 *
 * **One observer.** The obvious way to write a tile is to give it its own IntersectionObserver in
 * its setup and disconnect it on teardown. That is one observer per tile, and the browser has to
 * keep every one of them in step with every scroll: at three thousand tiles it is three thousand
 * separate pieces of bookkeeping for one scroll gesture. One observer watching three thousand
 * elements is one. The API was built for this and it is easy to use it the other way by accident.
 *
 * **A capped pool.** A <video> element is not markup, it is a decoder. Autoplay set to "everything
 * visible" on a large screen can put a hundred tiles on screen at once, and a hundred decoders will
 * exhaust memory and stall the compositor: on a modest laptop, which is the machine this has to
 * work on, it takes considerably fewer. So there is a fixed number of them, they are handed to
 * whichever tiles are most central, and every other visible tile shows its still. The cap is the
 * safety property: without it, the setting is a way for a user to hang their own browser.
 */

import { browser } from '$app/environment';
// The pool and the loaded-clip table are read during render, so they have to be collections
// Svelte watches. A plain Map works and never re-renders.
import { SvelteMap } from 'svelte/reactivity';

/*
 * How many videos may decode at once.
 *
 * Four, and it is a floor rather than a measurement: the number a weak machine can sustain, not
 * the number a strong one could. The visible failure of too few is that a tile shows a still
 * picture; the visible failure of too many is that the whole tab stops responding.
 */
export const VIDEO_POOL_CAP = 4;

/*
 * How many may decode at once when somebody has asked for everything visible.
 *
 * "Preview everything visible" is supposed to mean everything visible, and with the cap above it
 * would mean four: on a screen of forty tiles, thirty-six sitting still and the setting looking
 * broken. Four is not safe in general; it is what a machine can sustain when the pool is filling
 * itself from scrolling, unasked.
 *
 * This is the number for when it WAS asked for, and it is a real ceiling rather than a formality: a
 * full screen at the smallest tile size is around forty clips, and this covers that. Past it the
 * tab is at risk (a <video> is a decoder, not a picture), so the cap stays, and the tiles beyond
 * it keep their stills rather than the browser keeping none of them.
 */
export const VISIBLE_POOL_CAP = 48;

/* How far outside the viewport a tile starts loading. Roughly one row, so a clip is ready by the
 * time it is looked at without loading the whole library on a fast scroll. */
const ROOT_MARGIN = '300px 0px';

/* Where a share of the screen is read: the share of the tile on the screen itself, with no margin.
 * The margin's observer cannot answer it, since a tile anywhere in the margin is wholly inside that
 * observer's widened box and reads as a whole share, the same as a tile in the middle of the
 * screen. The share is what decides who keeps a video slot, so measured against the margin it would
 * give slots to tiles nobody could see yet, in the row below the screen, while tiles on it showed
 * their stills. */
const ON_SCREEN_THRESHOLDS = [0, 0.25, 0.6, 1];

/*
 * How many fetched clips are kept in memory at once, OFF the screen.
 *
 * Roughly a screen's worth. A browser holds a blob until its URL is revoked, so an unbounded
 * cache is a slow memory leak that only shows up after somebody has scrolled a long way, which
 * is exactly the session this grid exists to stay smooth during.
 *
 * It bounds what is kept for tiles that have gone, never what the tiles on screen are showing:
 * see `PreviewLoader`'s `keep`. Bounding both would put this below the 48 the pool allows once
 * everything visible is asked for, and clips on screen would be evicted as they arrived:
 * "Preview everything visible" would light only some of what is in view.
 */
export const READY_PREVIEW_CAP = 24;

type VisibilityListener = (visible: boolean, ratio: number) => void;

/**
 * The grid's observers, two for every tile however many tiles there are. Tiles subscribe; nothing
 * else creates one.
 *
 * `near` is the screen and a row either side of it: a tile there starts fetching its clip, and a
 * tile leaving it gives the fetch up. `seen` is the screen alone, and its share is the rank a tile
 * claims a video slot with, so the slots go to what is on the screen before the row waiting below
 * it (see `ON_SCREEN_THRESHOLDS`). A listener is told whether its tile is near, and its share of
 * the screen, whenever either answer moves.
 */
export class ViewportWatcher {
	#near: IntersectionObserver | null = null;
	#seen: IntersectionObserver | null = null;
	#listeners = new Map<Element, VisibilityListener>();
	#states = new Map<Element, { near: boolean; seen: number }>();

	/** How many observers exist. Two, or none before the first tile. */
	get observerCount(): number {
		return (this.#near ? 1 : 0) + (this.#seen ? 1 : 0);
	}

	get watchedCount(): number {
		return this.#listeners.size;
	}

	watch(element: Element, listener: VisibilityListener): () => void {
		this.#listeners.set(element, listener);
		this.#states.set(element, { near: false, seen: 0 });
		if (this.#ensure()) {
			this.#near?.observe(element);
			this.#seen?.observe(element);
		}

		return () => {
			this.#listeners.delete(element);
			this.#states.delete(element);
			this.#near?.unobserve(element);
			this.#seen?.unobserve(element);
		};
	}

	/** Tear the observers down. For a test, or a grid leaving the screen entirely. */
	disconnect(): void {
		this.#near?.disconnect();
		this.#seen?.disconnect();
		this.#near = null;
		this.#seen = null;
		this.#listeners.clear();
		this.#states.clear();
	}

	/** One answer moved: tell the tile both, the margin's yes or no and the screen's share. */
	#told(entry: IntersectionObserverEntry, which: 'near' | 'seen'): void {
		const state = this.#states.get(entry.target);
		if (!state) return;
		if (which === 'near') state.near = entry.isIntersecting;
		else state.seen = entry.isIntersecting ? entry.intersectionRatio : 0;
		/* On the screen is inside the margin too, whichever of the two the browser tells first. */
		if (state.seen > 0) state.near = true;
		this.#listeners.get(entry.target)?.(state.near, state.seen);
	}

	#ensure(): boolean {
		if (this.#near && this.#seen) return true;
		if (!browser || typeof IntersectionObserver === 'undefined') return false;

		this.#near = new IntersectionObserver(
			(entries) => {
				for (const entry of entries) this.#told(entry, 'near');
			},
			{ rootMargin: ROOT_MARGIN, threshold: [0] }
		);
		this.#seen = new IntersectionObserver(
			(entries) => {
				for (const entry of entries) this.#told(entry, 'seen');
			},
			{ threshold: ON_SCREEN_THRESHOLDS }
		);
		return true;
	}
}

/*
 * The claim a hovered tile makes.
 *
 * Higher than any share of the screen an ordinary visible tile can report, so hovering always wins
 * a slot, but finite, so a slot held by a hover can still be taken by another hover. An infinite
 * rank would pin the slot: with every slot held at infinity, the eviction scan can never find a
 * weaker holder, and no other tile gets to play until one of them scrolls off.
 */
export const HOVER_RANK = 2;

/**
 * Who currently holds one of the video slots.
 *
 * Claims are ranked, and the rank is how central the tile is on screen. When a better claim
 * arrives and every slot is taken, the weakest current holder gives one up, so the clips that
 * play are the ones being looked at, rather than whichever ones happened to be scrolled past
 * first.
 */
export class VideoPool {
	#cap: number;
	#holders = new SvelteMap<string, number>();

	constructor(cap: number = VIDEO_POOL_CAP) {
		this.#cap = cap;
	}

	get cap(): number {
		return this.#cap;
	}

	/** Change how many may play at once, dropping the weakest claims if the cap came down.
	 *
	 * For the autoplay setting, which is a different answer to "how many is safe": four while the
	 * pool fills itself from scrolling, many more when somebody has explicitly asked for everything
	 * on screen. Evicting on the way down rather than waiting for the next claim, or turning the
	 * setting off would leave forty videos decoding until something happened to scroll.
	 */
	resize(cap: number): void {
		this.#cap = Math.max(1, Math.floor(cap));
		while (this.#holders.size > this.#cap) {
			let weakestId: string | null = null;
			let weakestRank = Number.POSITIVE_INFINITY;
			for (const [held, heldRank] of this.#holders) {
				if (heldRank < weakestRank) {
					weakestId = held;
					weakestRank = heldRank;
				}
			}
			if (weakestId === null) return;
			this.#holders.delete(weakestId);
		}
	}

	get size(): number {
		return this.#holders.size;
	}

	holds(id: string): boolean {
		return this.#holders.has(id);
	}

	/** How many holders are moving: those `moving` says have their clip and want to play. A slot
	 *  held for a clip still on its way is a still picture, and saying it plays would be the
	 *  count running ahead of the screen. */
	moving(moving: (id: string) => boolean): number {
		let count = 0;
		for (const id of this.#holders.keys()) if (moving(id)) count += 1;
		return count;
	}

	/** Ask for a slot. Returns whether this tile now has one. */
	claim(id: string, rank: number): boolean {
		if (this.#holders.has(id)) {
			this.#holders.set(id, rank);
			return true;
		}

		if (this.#holders.size < this.#cap) {
			this.#holders.set(id, rank);
			return true;
		}

		let weakestId: string | null = null;
		let weakestRank = rank;
		for (const [held, heldRank] of this.#holders) {
			if (heldRank < weakestRank) {
				weakestId = held;
				weakestRank = heldRank;
			}
		}

		// Nobody is weaker than the asker, so the asker waits. This is the branch that holds the
		// cap: without it, a tile that could not evict anyone would be added anyway.
		if (weakestId === null) return false;

		this.#holders.delete(weakestId);
		this.#holders.set(id, rank);
		return true;
	}

	release(id: string): void {
		this.#holders.delete(id);
	}

	clear(): void {
		this.#holders.clear();
	}
}

/**
 * Fetching a preview clip, and giving up on it.
 *
 * The cancel is the whole point. A fast scroll through a large library crosses thousands of tiles
 * in a couple of seconds; each one starts a fetch as it approaches and, without a cancel, every
 * one of those fetches is still queued and still coming when the user has long since stopped
 * somewhere else. The connection pool fills with requests for clips nobody is looking at any more,
 * and the ones that are wanted queue behind them, so the grid gets slower the faster it is
 * scrolled, which is exactly backwards.
 */
export class PreviewLoader {
	#inFlight = new Map<string, AbortController>();
	#ready = new SvelteMap<string, string>();
	/* The address each held clip was fetched from. A clip rebuilt has a new address (its token is
	   the picture's digest), and the one held under the same id is then the old picture. */
	#fetchedFrom = new Map<string, string>();
	/* The address each missing clip was refused at. Kept by address, never by id alone: a clip
	   built or rebuilt changes the tile's picture token and with it the address, so a new address
	   is asked and the refused one is not, however often the wall offers the tile its clip. */
	#missing = new Map<string, string>();
	#cap: number;
	#keep: (id: string) => boolean;

	/**
	 * `keep` answers whether a tile on screen still wants this clip. A kept clip is never the one
	 * dropped, so the cap reclaims only what has gone off the screen and nothing a visible tile is
	 * playing disappears under it. What it may then hold is bounded by the screen, which is
	 * bounded by the page.
	 */
	constructor(cap: number = READY_PREVIEW_CAP, keep: (id: string) => boolean = () => false) {
		this.#cap = cap;
		this.#keep = keep;
	}

	get inFlightCount(): number {
		return this.#inFlight.size;
	}

	get readyCount(): number {
		return this.#ready.size;
	}

	/*
	 * Read only, and it has to stay that way: this is called while the grid renders, and touching
	 * the map here (to mark the entry as recently used, which is the obvious thing to want)
	 * would write to reactive state during a render and start a loop. Recency is recorded in
	 * `prefetch`, which runs from a scroll or a hover rather than from drawing.
	 */
	urlFor(id: string): string | undefined {
		return this.#ready.get(id);
	}

	/** Start fetching a clip, unless it is already loaded from this address or already coming.
	 *  A clip held from another address is the picture from before a rebuild: it stays on the tile
	 *  until the new one has arrived, and is let go then. */
	async prefetch(id: string, source: string, fetcher: typeof fetch = fetch): Promise<void> {
		if (this.#ready.has(id) && this.#fetchedFrom.get(id) === source) {
			// Already here, and asking for it again is the grid saying it is wanted: move it to the
			// young end so it is not the next one dropped.
			const url = this.#ready.get(id) as string;
			this.#ready.delete(id);
			this.#ready.set(id, url);
			return;
		}
		if (this.#inFlight.has(id) || this.#missing.get(id) === source) return;

		const controller = new AbortController();
		this.#inFlight.set(id, controller);

		try {
			const response = await fetcher(source, { signal: controller.signal });
			if (!response.ok) {
				// There is no clip at this address: a still image has none, and a clip's file can
				// be gone from the disk. Remembered with its address, because without it every pass
				// of the tile through the viewport, and every re-read while a task runs, asks again
				// and the wall spends one refused request per tile each time. A clip that is built
				// later arrives under a new address (see `#missing`), which is asked.
				this.#missing.set(id, source);
				return;
			}
			const blob = await response.blob();
			// It may have been cancelled while the body was being read.
			if (controller.signal.aborted) return;
			const before = this.#ready.get(id);
			if (before !== undefined) {
				URL.revokeObjectURL(before);
				this.#ready.delete(id);
			}
			this.#ready.set(id, URL.createObjectURL(blob));
			this.#fetchedFrom.set(id, source);
			this.#evictDownTo();
		} catch {
			// An aborted fetch throws, and so does a network failure. Neither is worth showing
			// anybody: the tile keeps its still picture, which is a complete thing to look at.
		} finally {
			// Only if it is still ours. A cancel followed by a fresh request leaves a *newer*
			// controller under this id, and deleting that one blindly would leave the new request
			// with nothing to cancel it, and the request after that starting a duplicate.
			if (this.#inFlight.get(id) === controller) this.#inFlight.delete(id);
		}
	}

	/** Stop fetching this clip. Called when a tile leaves the viewport. */
	cancel(id: string): void {
		const controller = this.#inFlight.get(id);
		if (!controller) return;
		controller.abort();
		this.#inFlight.delete(id);
	}

	/*
	 * Forget that a clip was missing, so the same address may be asked for again. One id, or all
	 * of them.
	 *
	 * The wall never needs this to show a clip built after its tile was first seen: building it
	 * moves the tile's picture token, so the clip has a new address and `prefetch` asks for it.
	 * Forgetting on every re-read would ask each refused address again once a second while a task
	 * runs, which is the request storm `#missing` exists to prevent.
	 */
	forget(id?: string): void {
		if (id === undefined) this.#missing.clear();
		else this.#missing.delete(id);
	}

	/*
	 * Keep only a few screens' worth of decoded clips.
	 *
	 * Without this the grid holds every preview it has ever fetched for as long as the tab is open.
	 * A clip is a fifth of a megabyte or so, a library runs to thousands of items, and a browser
	 * does not free a blob until its URL is revoked, so a long scroll quietly accumulates
	 * hundreds of megabytes and the machine this is supposed to stay fluid on is the one that
	 * notices first. Nothing looks wrong while it happens, which is why it needs a number rather
	 * than an intention.
	 */
	#evictDownTo(): void {
		if (this.#ready.size <= this.#cap) return;
		// Oldest first, passing over every clip a tile on screen still wants (see `keep`).
		for (const [id, url] of [...this.#ready]) {
			if (this.#ready.size <= this.#cap) return;
			if (this.#keep(id)) continue;
			URL.revokeObjectURL(url);
			this.#ready.delete(id);
			this.#fetchedFrom.delete(id);
		}
	}

	/** Release every object URL. A grid that is going away must not leak its blobs. */
	dispose(): void {
		for (const controller of this.#inFlight.values()) controller.abort();
		this.#inFlight.clear();
		for (const url of this.#ready.values()) URL.revokeObjectURL(url);
		this.#ready.clear();
		this.#fetchedFrom.clear();
		this.#missing.clear();
	}
}
