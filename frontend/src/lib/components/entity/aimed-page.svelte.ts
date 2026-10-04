/*
 * Whether the screen somebody is on is ABOUT one thing, so a link dropped anywhere on it has an
 * obvious place to go.
 *
 * A link dropped on a person's own page means "file this under them", and two components need that
 * answer: the page's offer, which takes the drop, and the window-wide offer, which must stand down
 * so one gesture starts one download. A module rather than a `data-drop-zone` element, because a
 * whole page is not a box and wrapping its layout would change each entity page differently. One
 * page is on screen at a time, so one value. The page clears it on unmount, or the next drop would
 * aim at whoever was looked at before.
 */

import type { AimedAt } from '$lib/library/aimed-drop.svelte';

/** What the screen on show is about, or null on a screen that is about nothing in particular. */
interface PageAim {
	kind: AimedAt;
	id: string;
	/** What to call it on screen. A drop on the wrong page must not look like the right one. */
	name: string;
}

let aim = $state<PageAim | null>(null);

/** Say what this screen is about. Called by the page while it is mounted, and nowhere else. */
export function aimPageAt(at: PageAim | null): void {
	aim = at;
}

/** What the screen on show is about, for a surface deciding what a dropped link means. */
export function pageAim(): PageAim | null {
	return aim;
}
