/*
 * One IntersectionObserver for the whole grid, not one per tile, and a capped pool of video
 * elements given to the most central tiles: each is a decoder, and too many hang the tab.
 */

import { browser } from '$app/environment';
// Read during render, so collections Svelte watches.
import { SvelteMap } from 'svelte/reactivity';

/* A floor a weak machine can sustain: too few shows a still, too many freezes the tab. */
export const VIDEO_POOL_CAP = 4;

/*
 * When everything visible was asked for: a full screen at the smallest tiles, and still a ceiling.
 */
export const VISIBLE_POOL_CAP = 48;

/* About a row, so a clip is ready when it is looked at. */
const ROOT_MARGIN = '300px 0px';

/* The share is read with no margin, or tiles below the screen would win slots. */
const ON_SCREEN_THRESHOLDS = [0, 0.25, 0.6, 1];

/* Clips kept OFF the screen, bounded since a blob lives until revoked; never what is on screen. */
export const READY_PREVIEW_CAP = 24;

type VisibilityListener = (visible: boolean, ratio: number) => void;

/** Two observers for every tile: `near` (fetch) and `seen` (the share a slot is ranked by). */
export class ViewportWatcher {
	#near: IntersectionObserver | null = null;
	#seen: IntersectionObserver | null = null;
	#listeners = new Map<Element, VisibilityListener>();
	#states = new Map<Element, { near: boolean; seen: number }>();

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

	disconnect(): void {
		this.#near?.disconnect();
		this.#seen?.disconnect();
		this.#near = null;
		this.#seen = null;
		this.#listeners.clear();
		this.#states.clear();
	}

	#told(entry: IntersectionObserverEntry, which: 'near' | 'seen'): void {
		const state = this.#states.get(entry.target);
		if (!state) return;
		if (which === 'near') state.near = entry.isIntersecting;
		else state.seen = entry.isIntersecting ? entry.intersectionRatio : 0;
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

/** Above any visible share, so a hover wins; finite, so another hover can take it. */
export const HOVER_RANK = 2;

/** Ranked by how central a tile is; the weakest holder gives up its slot. */
export class VideoPool {
	#cap: number;
	#holders = new SvelteMap<string, number>();

	constructor(cap: number = VIDEO_POOL_CAP) {
		this.#cap = cap;
	}

	get cap(): number {
		return this.#cap;
	}

	/** Evicting on the way down, or turning the setting off would leave forty decoding. */
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

	/** Only those with their clip and wanting to play. */
	moving(moving: (id: string) => boolean): number {
		let count = 0;
		for (const id of this.#holders.keys()) if (moving(id)) count += 1;
		return count;
	}

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

		// Nobody is weaker, so the asker waits: this holds the cap.
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
 * The cancel is the point: otherwise a fast scroll queues fetches nobody wants, ahead of those
 * wanted.
 */
export class PreviewLoader {
	#inFlight = new Map<string, AbortController>();
	#ready = new SvelteMap<string, string>();
	/* A rebuilt clip has a new address. */
	#fetchedFrom = new Map<string, string>();
	/* Kept by address, so a rebuilt clip is asked and the refused one is not. */
	#missing = new Map<string, string>();
	#cap: number;
	#keep: (id: string) => boolean;

	/** `keep`: a clip a tile on screen wants is never the one dropped. */
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

	/* Read only: called during render, so recency is recorded in `prefetch`. */
	urlFor(id: string): string | undefined {
		return this.#ready.get(id);
	}

	/** A clip from an older address stays on the tile until the new one arrives. */
	async prefetch(id: string, source: string, fetcher: typeof fetch = fetch): Promise<void> {
		if (this.#ready.has(id) && this.#fetchedFrom.get(id) === source) {
			// Asked again, so moved to the young end.
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
				// No clip at this address; remembered by address, or every pass asks again.
				this.#missing.set(id, source);
				return;
			}
			const blob = await response.blob();
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
			// The tile keeps its still.
		} finally {
			// Only if it is still ours: a newer request may have replaced it.
			if (this.#inFlight.get(id) === controller) this.#inFlight.delete(id);
		}
	}

	cancel(id: string): void {
		const controller = this.#inFlight.get(id);
		if (!controller) return;
		controller.abort();
		this.#inFlight.delete(id);
	}

	/*
	 * Never needed for a clip built later, which has a new address; forgetting on re-reads would
	 * storm.
	 */
	forget(id?: string): void {
		if (id === undefined) this.#missing.clear();
		else this.#missing.delete(id);
	}

	/* A few screens' worth of clips: blobs live until revoked. */
	#evictDownTo(): void {
		if (this.#ready.size <= this.#cap) return;
		for (const [id, url] of [...this.#ready]) {
			if (this.#ready.size <= this.#cap) return;
			if (this.#keep(id)) continue;
			URL.revokeObjectURL(url);
			this.#ready.delete(id);
			this.#fetchedFrom.delete(id);
		}
	}

	dispose(): void {
		for (const controller of this.#inFlight.values()) controller.abort();
		this.#inFlight.clear();
		for (const url of this.#ready.values()) URL.revokeObjectURL(url);
		this.#ready.clear();
		this.#fetchedFrom.clear();
		this.#missing.clear();
	}
}
