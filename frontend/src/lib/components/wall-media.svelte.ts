/**
 * What the tiles of a wall of files draw and play: the still each one wears, and which of them play
 * their clip. One shared observer watches every tile, and a capped pool decides how many videos
 * decode at once, so the tiles beyond the cap keep their stills. The wall never asks for a transcode.
 */

import { untrack } from 'svelte';
import { clipWasBuilt, previewUrl, rowStillUrl, thumbUrl } from '$lib/entity/art';
import { gridAutoplay, type Grid, type GridItem, type RowSource } from '$lib/grid/grid.svelte';
import {
	HOVER_RANK,
	PreviewLoader,
	READY_PREVIEW_CAP,
	VIDEO_POOL_CAP,
	VISIBLE_POOL_CAP,
	VideoPool,
	ViewportWatcher
} from '$lib/grid/viewport.svelte';
import { imports } from '$lib/library/imports.svelte';

/** The picture a tile draws: its file's still, or a mark's own frame, so two marks are two pictures. */
export function stillFor(
	item: GridItem,
	source: RowSource,
	fileOf: (item: GridItem) => string
): string | undefined {
	if (item.concealed || !item.thumb) return undefined;
	if (item.still && source?.path) return rowStillUrl(source.path, item);
	return thumbUrl({ id: fileOf(item), art: item.art });
}

/**
 * What an empty tile means when nothing will fill it, or undefined while something is: a shimmer
 * that never resolves is a lie (`Tile.placeholder`).
 */
export function stillMissing(item: GridItem): string | undefined {
	if (item.concealed || item.thumb) return undefined;
	// No copy anywhere Sift can reach waits for nothing, so it is asked before `imports.busy`.
	if (item.unreachable) return "Can't reach this file";
	if (imports.busy > 0) return undefined;
	// A file a feature has given up on is not "yet": the tile says so, with the reason.
	if (item.verdict) return 'No picture';
	return item.width ? 'No picture yet' : 'Not read yet';
}

/** Whether a tile's file is still arriving (`Tile.importing`); a gone file is not. */
export function fileArriving(item: GridItem): boolean {
	return !item.concealed && !item.thumb && !item.unreachable;
}

/** Which tiles of one wall are on screen, and which of them play. */
export class WallMedia {
	/* Which tiles are on screen and how central: the observer speaks only on a change. */
	readonly onScreen = new Map<string, number>();
	readonly watcher = new ViewportWatcher();
	readonly pool = new VideoPool();
	/* A clip a tile on screen wants is never the one dropped (see `keep`). */
	readonly previews = new PreviewLoader(READY_PREVIEW_CAP, (id) => this.onScreen.has(id));
	hovered = $state<string | null>(null);
	private grid: Grid;
	private fileOf: (item: GridItem) => string;
	/** Told when the newest file on the page comes into view, which lets waiting arrivals in. */
	private onNewestSeen: () => void;

	constructor(grid: Grid, fileOf: (item: GridItem) => string, onNewestSeen: () => void) {
		this.grid = grid;
		this.fileOf = fileOf;
		this.onNewestSeen = onNewestSeen;
		/* Offered again whenever the page is read: a tile whose clip was built while it sat in view
		   never comes into view again. Asking again for a clip held costs nothing. */
		$effect(() => {
			void grid.items;
			untrack(() => this.offerClips());
		});
		/* How many previews are moving, for the preview control's tooltip. */
		$effect(() =>
			gridAutoplay.counts(() =>
				this.pool.moving((id) => this.wants(id) && this.previews.urlFor(id) !== undefined)
			)
		);
		/* What plays changes now rather than at the next scroll, wherever the choice was made. */
		$effect(() => {
			void gridAutoplay.mode;
			untrack(() => this.applyAutoplay());
		});
		$effect(() => () => {
			this.watcher.disconnect();
			this.previews.dispose();
		});
	}

	/** Whether this tile should be playing: everything visible, or the one under the pointer. */
	wants(id: string): boolean {
		return gridAutoplay.mode === 'visible' || this.hovered === id;
	}

	/** Whether a tile is drawn playing: a slot, a wish to play. */
	playing(id: string): boolean {
		return this.pool.holds(id) && this.wants(id);
	}

	/** Whether this row has a hover clip that was built, so a scan that builds none asks nothing. */
	hasPreview(id: string): boolean {
		const item = this.grid.byId.get(id);
		if (item === undefined) return false;
		return clipWasBuilt(item);
	}

	/** The clip's address, with the row's token on it so the browser may keep it. The FILE's clip:
	 *  a moment has none of its own. */
	previewFor(id: string): string {
		const item = this.grid.byId.get(id);
		if (item === undefined) return previewUrl({ id });
		return previewUrl({ id: this.fileOf(item), art: item.art });
	}

	/** Subscribe one tile to the shared observer. */
	observe(element: Element, id: string): () => void {
		return this.watcher.watch(element, (visible, ratio) => this.tileVisible(id, visible, ratio));
	}

	private tileVisible(id: string, visible: boolean, ratio: number): void {
		if (visible) this.onScreen.set(id, ratio);
		else this.onScreen.delete(id);

		/* Scrolling back to the top is the other way waiting arrivals become takeable. */
		if (id === this.grid.items[0]?.id) this.onNewestSeen();

		if (!visible) {
			// Leaving cancels the fetch, or a fast scroll queues one for every tile it crosses.
			this.previews.cancel(id);
			this.pool.release(id);
			return;
		}

		if (!this.hasPreview(id)) return;
		void this.previews.prefetch(id, this.previewFor(id));
		// Only something with a clip may hold one of the few slots.
		if (this.wants(id)) this.pool.claim(id, ratio);
	}

	tileHovered(id: string, hovering: boolean): void {
		this.hovered = hovering ? id : this.hovered === id ? null : this.hovered;
		if (hovering) {
			if (!this.hasPreview(id)) return;
			void this.previews.prefetch(id, this.previewFor(id));
			// Outranks anything merely visible, by a finite amount.
			this.pool.claim(id, HOVER_RANK);
		} else if (this.wants(id)) {
			// Still wanted: it competes on how much of it is on screen, like every visible tile.
			this.pool.claim(id, this.onScreen.get(id) ?? 0);
		} else {
			this.pool.release(id);
		}
	}

	/*
	 * Clear the pool and re-offer what is on screen. The choice also says how many videos this
	 * machine is asked to decode: four for a pool filling itself from scrolling, closer to forty for
	 * everything visible on a full screen.
	 */
	private applyAutoplay(): void {
		const all = gridAutoplay.mode === 'visible';
		this.pool.resize(all ? VISIBLE_POOL_CAP : VIDEO_POOL_CAP);
		this.pool.clear();
		if (!all) return;
		for (const [id, ratio] of this.onScreen) {
			// Skipped entirely: a tile with no clip that takes a slot takes it from one with a clip.
			if (!this.hasPreview(id)) continue;
			void this.previews.prefetch(id, this.previewFor(id));
			this.pool.claim(id, ratio);
		}
	}

	private offerClips(): void {
		for (const [id, ratio] of this.onScreen) {
			if (!this.hasPreview(id)) continue;
			void this.previews.prefetch(id, this.previewFor(id));
			if (this.wants(id)) this.pool.claim(id, this.hovered === id ? HOVER_RANK : ratio);
		}
	}

	/*
	 * Offer every tile on screen its clip again when work settles. A clip built since has a new
	 * address, so it is fetched; one that answered 404 stays set aside until its token moves.
	 */
	rearm(): void {
		for (const id of this.onScreen.keys()) {
			if (!this.hasPreview(id)) continue;
			void this.previews.prefetch(id, this.previewFor(id));
		}
	}
}
