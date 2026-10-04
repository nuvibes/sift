/*
 * The trail of the screen on view, said by its frame and drawn by the top bar.
 *
 * A screen hands its trail to its frame (`PageFrame.crumbs`), and on a desk the frame says it here
 * rather than drawing a band of its own: the trail stands in the top bar, on the search box's
 * line, so a screen with a trail starts where every other screen starts. On a phone the bar has no
 * room beside its squares and the frame draws the trail as the page's first line instead, saying
 * nothing here.
 *
 * Owned, like the screen bar: two frames are alive for a moment while one screen replaces another,
 * in either order, and only the frame that said the trail may take it back. Without that, the old
 * screen going away after the new one arrived would empty the new screen's trail.
 */
import type { Crumb } from '$lib/components/common/Breadcrumbs.svelte';

class PageTrail {
	#owner: symbol | null = null;

	/** The trail on view, outermost first; empty where the screen has none to show. */
	crumbs = $state<Crumb[]>([]);

	/** Say `crumbs` as the trail on view, for the frame `owner`. */
	say(owner: symbol, crumbs: Crumb[]): void {
		this.#owner = owner;
		this.crumbs = crumbs;
	}

	/** Take it back, if `owner` is still the frame that said it. */
	unsay(owner: symbol): void {
		if (this.#owner !== owner) return;
		this.#owner = null;
		this.crumbs = [];
	}
}

export const pageTrail = new PageTrail();
