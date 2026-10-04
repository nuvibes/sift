// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Where a setting is, written the way a person reads it: `Settings > Privacy > Auto-lock > Lock
 * Hidden when you switch away`.
 *
 * ONE writer and ONE reader. The writer is what "Copy settings path" puts on the clipboard; the
 * reader is what the settings search does with the same words pasted back (`landing` in
 * `search.ts`). Both use the section names from `sections.ts` and the names drawn on the pane, so
 * a path somebody copied is a path the search can follow.
 *
 * ## Where the crumbs come from
 *
 * Each level of a pane says its own name to what it holds, through Svelte context: the pane says
 * the section, a sub-page says its title and the press that opened it, a group says its heading.
 * A row adds its own name at the end. Nothing is written twice: the words are the ones already on
 * screen, so a renamed heading renames every path under it in the same edit.
 */

import { getContext, setContext } from 'svelte';

/** The first crumb of every path: the screen's own name. */
const PATH_ROOT = 'Settings';

/** Between two crumbs. */
const PATH_JOIN = ' > ';

/** Where a copy button stands: the crumbs above it, and the heading of the group it is in. */
export interface Place {
	/** The crumbs from the section down to (not including) the nearest group heading. */
	trail: () => string[];
	/** The heading of the group this is in, where the group has one. */
	group?: () => string | undefined;
}

const PLACE = Symbol('settings-place');

/** A path, from its crumbs after `Settings`. Empty crumbs are left out. */
export function pathOf(crumbs: readonly (string | undefined)[]): string {
	const said = crumbs.map((crumb) => crumb?.trim() ?? '').filter(Boolean);
	return [PATH_ROOT, ...said].join(PATH_JOIN);
}

/**
 * The crumbs after `Settings` in something typed or pasted, or null when it is not a path.
 *
 * Forgiving about what a copy picks up on the way: code ticks or quotes around it, any spacing
 * around the separators, the single angle quote some editors turn `>` into, a trailing separator.
 * The leading `Settings` may be left off (`Playback > Theater > Default`): somebody writing a path
 * down from memory starts at the section, and the separator is what says it is a path. Without the
 * root it names at least two crumbs, so a word with a stray `>` after it stays a search. What an
 * ordinary search never has is the separator, so it is never read as a path.
 */
export function crumbsOf(text: string): string[] | null {
	const bare = text.trim().replace(/^[`'"]+|[`'"]+$/g, '');
	if (!/[>\u203a]/.test(bare)) return null;
	const crumbs = bare
		.split(/[>\u203a]/)
		.map((crumb) => crumb.trim())
		.filter(Boolean);
	if (crumbs[0]?.toLowerCase() === PATH_ROOT.toLowerCase()) {
		return crumbs.length < 2 ? null : crumbs.slice(1);
	}
	return crumbs.length < 2 ? null : crumbs;
}

/** Where this component stands, or undefined outside Settings (no copy button is drawn there). */
export function placeHere(): Place | undefined {
	return getContext<Place | undefined>(PLACE);
}

/** The pane: the section's own name starts every path under it. */
export function sectionPlace(label: () => string): void {
	setContext<Place>(PLACE, { trail: () => [label()] });
}

/** A sub-page: its title and the press that opened it follow whatever is above it. */
export function pagePlace(crumbs: () => (string | undefined)[]): void {
	const above = placeHere();
	setContext<Place>(PLACE, {
		trail: () => [...(above ? full(above) : []), ...crumbs().filter(isSaid)]
	});
}

/** A group: its heading stands between what is above and each row in it. */
export function groupPlace(heading: () => string | undefined): void {
	const above = placeHere();
	if (!above) return;
	setContext<Place>(PLACE, { trail: () => full(above), group: heading });
}

/** The path of a ROW named `name` standing here: through the group's heading. */
export function rowPath(place: Place, name: string): string {
	return pathOf([...full(place), name]);
}

/** The path of a HEADING named `name` standing here: its own group is the one it names. */
export function headingPath(place: Place, name: string): string {
	return pathOf([...place.trail(), name]);
}

/** The path of the page itself: a section's or a sub-page's title. */
export function pagePath(place: Place): string {
	return pathOf(place.trail());
}

function full(place: Place): string[] {
	const group = place.group?.();
	return group ? [...place.trail(), group] : place.trail();
}

function isSaid(crumb: string | undefined): crumb is string {
	return Boolean(crumb?.trim());
}
