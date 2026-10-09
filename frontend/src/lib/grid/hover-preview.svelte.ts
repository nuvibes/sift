/*
 * The hover clip for search results and a collection, which draw `Tile` themselves. Only what is
 * under the cursor plays, so `AssetGrid`'s slot pool is not needed; the fetching is shared
 * (`PreviewLoader`), since a clip still being built answers 404.
 */

import { previewUrl, type Pictured } from '$lib/entity/art';
import { PreviewLoader } from '$lib/grid/viewport.svelte';

/** A still has no clip and a concealed file serves nothing; a GIF has one. */
export function canPreview(item: { media_type: string; concealed?: boolean }): boolean {
	if (item.concealed) return false;
	return item.media_type === 'video' || item.media_type === 'gif';
}

export class HoverPreviews {
	hovered = $state<string | null>(null);

	#loader = new PreviewLoader();

	/** The whole row: the clip's address carries a token off it. */
	enter(item: Pictured, hovering: boolean, previewable: boolean): void {
		const id = item.id;
		if (!hovering) {
			// Only if it is still ours: leaving A and entering B arrive in that order.
			if (this.hovered === id) this.hovered = null;
			return;
		}
		this.hovered = id;
		if (!previewable) return;
		void this.#loader.prefetch(id, previewUrl(item));
	}

	/** Undefined until it has arrived, so the tile keeps its still. */
	srcFor(id: string): string | undefined {
		return this.#loader.urlFor(id);
	}

	playing(id: string): boolean {
		return this.hovered === id;
	}

	dispose(): void {
		this.hovered = null;
		this.#loader.dispose();
	}
}
