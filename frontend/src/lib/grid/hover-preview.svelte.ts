/*
 * The hover clip, for the two walls that are not the main grid.
 *
 * Search results and the inside of a collection lay their tiles out with the same justified rows
 * the library uses, but they are not `AssetGrid`: they fetch their own rows, so they draw `Tile`
 * themselves. Without this they would get the still and nothing else: pointing at a video on
 * either of those screens would show a frozen first frame, on a library where the same tile moves
 * everywhere else, which is what happens when a capability lives inside one component and a second
 * screen is built beside it.
 *
 * This is the smallest honest share of it. `AssetGrid` runs a much larger machine: a pool of
 * video slots handed out by how central a tile is, so a screenful of tiles decodes four clips and
 * not forty, and a scroll cancels what it crossed. That machine exists because that grid plays
 * things nobody is pointing at. These two screens only ever play what is under the cursor, so the
 * pool would be a pool of one and the scroll cancelling would have nothing to cancel.
 *
 * So what is shared is the FETCHING, which is the part with the real failure mode in it: a clip
 * that is still being built answers 404, and a screen that asked once and gave up shows a frozen
 * tile for the rest of the session. `PreviewLoader` is the one place that is handled, and it is
 * reused here rather than reimplemented.
 */

import { previewUrl, type Pictured } from '$lib/entity/art';
import { PreviewLoader } from '$lib/grid/viewport.svelte';

/** What a tile has to be for there to be a clip at all. A still has none; a concealed file serves
 *  nothing. A GIF has one: it moves, so the import builds it a preview exactly as for a video. */
export function canPreview(item: { media_type: string; concealed?: boolean }): boolean {
	if (item.concealed) return false;
	return item.media_type === 'video' || item.media_type === 'gif';
}

export class HoverPreviews {
	/** Which tile the cursor is on, or null. One at a time, which is the whole simplification. */
	hovered = $state<string | null>(null);

	#loader = new PreviewLoader();

	/** The cursor arrived at or left a tile. `previewable` is asked by the caller because only the
	 *  caller knows what the row it drew actually is.
	 *
	 *  The whole row rather than its id, because the clip's address carries a token off that row:
	 *  without it the browser re-checks the clip every visit. Taking the id alone would make that
	 *  impossible to pass and easy not to notice. */
	enter(item: Pictured, hovering: boolean, previewable: boolean): void {
		const id = item.id;
		if (!hovering) {
			// Only if it is still ours. The pointer leaving A and entering B arrives in that order,
			// so clearing unconditionally would wipe the tile that had just been entered.
			if (this.hovered === id) this.hovered = null;
			return;
		}
		this.hovered = id;
		if (!previewable) return;
		void this.#loader.prefetch(id, previewUrl(item));
	}

	/** The clip's address, once it has arrived. Undefined until then, which is what makes the tile
	 *  go on showing its still rather than a gap. */
	srcFor(id: string): string | undefined {
		return this.#loader.urlFor(id);
	}

	/** Whether this tile should be playing. Only the hovered one ever is. */
	playing(id: string): boolean {
		return this.hovered === id;
	}

	/** Give back every object url this held. Called when the screen goes. */
	dispose(): void {
		this.hovered = null;
		this.#loader.dispose();
	}
}
