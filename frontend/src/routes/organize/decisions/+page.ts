import { redirect } from '@sveltejs/kit';

import { resolveAddress, settingsPath } from '$lib/settings-ui/sections';

/*
 * The decision record's old address: History, showing the decisions alone.
 *
 * What was decided on Organize and what Sift filed by itself are lines of History already, each
 * with its Undo, so a screen of its own for them would be a second place to look for one stream. The
 * address lives on in links, bookmarks and open tabs, so it moves rather than dies: 308, the move
 * is permanent, to `Settings > Tasks and Activity > App History` with Show on Decisions: the key
 * `activity.decisions`, which Activity opens on that tab and that choice, the same address
 * Organize's own Decisions link carries.
 *
 * No `+page.svelte` beside this: there is nothing left here to draw.
 */
export function load(): void {
	redirect(308, settingsPath(resolveAddress('tasks', 'activity.decisions')));
}
