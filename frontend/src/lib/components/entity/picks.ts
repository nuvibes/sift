/*
 * The cards picked on an entity page's tabs, as the filter that filters that page's files.
 *
 * On a person's page, pressing Jane Else's picture on the Seen with tab and the "beach" card on the
 * Tags tab asks one question: which of this person's files also have Jane Else on them and carry
 * "beach". A pick is that filter the moment it is made.
 *
 * The picks are the filter parameters, in the address: one `people=` / `tags=` / `sites=` /
 * `collections=` / `photo_sets=` parameter per picked card, each the card's name written the way
 * the query language reads it back (`asFilterValue`): `?show=people&people=Jane+Else&tags=beach`.
 * Every tab carries them (`carryPicks`), the Files tab included, so it is filtered the moment it is
 * opened and its chips are the same parameters drawn out, removable one at a time. One set of
 * parameters means the card tabs and the Files tab cannot disagree about what is picked.
 *
 * Nothing on a card tab reads these as a filter: the wall of cards is fetched by `loadRelated`,
 * which takes none of them, and the bar draws no chips on a wall that says it is not filterable
 * (`RelatedWall`'s `screenBar.publish`). A pick filters the files and nothing else.
 *
 * A value is a pick when it names one thing plainly, a bare or quoted name. A value with a minus
 * (not this), a pipe (any of these) or a comma (all of these, in one value) is a filter somebody
 * typed; it is carried untouched, and no card wears it as picked, since a card cannot say "every
 * file without her" by being washed in the accent.
 *
 * Names, not ids: the query language resolves names (the search slice's `_lookup`), as does a
 * card's link (`relatedHref`). Two things sharing a name filter to both, the wider and visible
 * direction, since the chip says the name. The page's own Files query names its subject by id.
 *
 * Every named parameter combines with AND on the server (`parse_modal`), and a repeated parameter
 * demands every value, so `people=Jane+Else&people=Grace+Hopper&tags=beach` beside the page's own
 * `people: <this person>` is all four at once. `bothNarrowings` joins the page's own constraint to
 * the address's without either replacing the other.
 */

import type { components } from '$lib/api/schema';
import { api } from '$lib/api/client';
import { bothNarrowings } from '$lib/components/shell/facet-labels';
import { fieldOf } from '$lib/components/shell/facet-labels';
import { kindOf, type RelatedKind } from '$lib/entity/related.svelte';

/** The query-language fields a card on an entity tab can stand for. */
type PickField = 'people' | 'tags' | 'sites' | 'collections' | 'photo_sets' | 'songs';

/* In the order a page's tabs run, so the picks read back in a stable order whatever the address.
   `songs` is a Music tab's card: the query language's own field for the song a file carries. */
const PICK_FIELDS: readonly PickField[] = [
	'people',
	'tags',
	'sites',
	'collections',
	'photo_sets',
	'songs'
];

export interface Pick {
	field: PickField;
	name: string;
}

/**
 * The field a card on this tab stands for, or null when a card here cannot be picked.
 *
 * Null for `files` and `loops`, which are walls of media rather than named things, and for
 * `sites_within`, a site's Sites tab, listing sites that are part of this one by a column on their
 * rows. A file comes from one site, so this site and a site inside it is nearly always an empty
 * wall, and a pick there would empty the screen. Its cards stay plain links.
 *
 * A collection's page picks like every other: its contents route reads the address's filters
 * (`contentsAsked`) in the collection's own arranged order.
 */
export function pickFieldOf(showing: RelatedKind): PickField | null {
	if (showing === 'sites_within' || showing === 'tags_within') return null;
	const kind = kindOf(showing);
	if (!kind) return null;
	const field = fieldOf(kind);
	return (PICK_FIELDS as readonly string[]).includes(field) ? (field as PickField) : null;
}

/**
 * One name as a value of a filter parameter, written so the parser reads it back as that name.
 *
 * The parser splits a value on commas (ALL of these) and pipes (ANY of these), and reads a leading
 * minus as "not": `_values`, `_alternatives` and `_listed` on the server. A name holding any of
 * those is quoted, which is the language's own rule for writing one (the quotes are dropped on the
 * way in). A quote inside a name cannot be written at all (the language has no escape) so it is
 * dropped, which asks for a name that does not exist and finds nothing: the narrow direction, the
 * same limitation `quoted` in `search.svelte` records. An ordinary name, spaces and all, goes as it
 * is, so the chip on the bar reads exactly what the card said.
 */
export function asFilterValue(name: string): string {
	const bare = name.replaceAll('"', '').trim();
	return /[,|]/.test(bare) || bare.startsWith('-') ? `"${bare}"` : bare;
}

/**
 * The one name a filter value picks, or null when it is not a pick. See "Which values are picks".
 *
 * The inverse of `asFilterValue`: a quoted value is the name inside the quotes, a bare one is
 * itself, and a bare one holding the language's operators is somebody's typed filter.
 */
function pickedName(value: string): string | null {
	const held = value.trim();
	if (held.length >= 2 && held.startsWith('"') && held.endsWith('"')) {
		const inner = held.slice(1, -1).trim();
		return inner && !inner.includes('"') ? inner : null;
	}
	if (!held || held.startsWith('-') || /[,|"]/.test(held)) return null;
	return held;
}

/** Every pick an address carries, field by field in tab order, each once. */
export function picksOf(url: URL): Pick[] {
	const picks: Pick[] = [];
	for (const field of PICK_FIELDS) {
		const seen = new Set<string>();
		for (const value of url.searchParams.getAll(field)) {
			const name = pickedName(value);
			if (name === null || seen.has(name)) continue;
			seen.add(name);
			picks.push({ field, name });
		}
	}
	return picks;
}

/**
 * The address's filter, as a listing that reads these five fields takes it: every value of each
 * field present, in the address's own order, a typed one (a minus, a pipe) included.
 *
 * Typed values too, and that is the same rule `carryPicks` follows: a filter somebody put on these
 * fields is in force where the picks are, and the bar draws a chip for it: a chip whose filter the
 * listing silently dropped would be a filter in force on the bar and nowhere else. The collection
 * page sends the whole address's filters (`contentsAsked`); the other entity pages' Files tabs read
 * the same parameters through the media grid (`AssetGrid`'s `fromAddress`).
 */
export function narrowingOf(url: URL): Record<string, string[]> {
	const asked: Record<string, string[]> = {};
	for (const field of PICK_FIELDS) {
		const values = url.searchParams.getAll(field);
		if (values.length > 0) asked[field] = values;
	}
	return asked;
}

/** Whether this card is among the picks. */
export function isPicked(picks: readonly Pick[], field: PickField, name: string): boolean {
	return picks.some((one) => one.field === field && one.name === name.trim());
}

/** The address as a path and a search, with `params` as its search. */
function spelled(url: URL, params: URLSearchParams): string {
	const search = params.toString();
	return `${url.pathname}${search ? `?${search}` : ''}`;
}

/**
 * The same parameters, in the same order, with the picks `drop` names taken off: every other value
 * kept, a typed exclusion on the same field included, which is not a pick and not this press's to
 * remove.
 */
function without(params: URLSearchParams, drop: (field: PickField, name: string) => boolean) {
	return new URLSearchParams(
		[...params].filter(([key, value]) => {
			if (!(PICK_FIELDS as readonly string[]).includes(key)) return true;
			const picked = pickedName(value);
			return picked === null || !drop(key as PickField, picked);
		})
	);
}

/** This address with one card picked, or un-picked if it already was. */
export function togglePick(url: URL, pick: Pick): string {
	const name = pick.name.trim();
	if (isPicked(picksOf(url), pick.field, name)) {
		const kept = without(url.searchParams, (field, held) => field === pick.field && held === name);
		return spelled(url, kept);
	}
	const params = new URLSearchParams(url.search);
	params.append(pick.field, asFilterValue(name));
	return spelled(url, params);
}

/** This address with nothing picked. Typed filters on the same fields stay. */
export function clearPicks(url: URL): string {
	return spelled(
		url,
		without(url.searchParams, () => true)
	);
}

/**
 * A TAB's address, carrying the picks along: EVERY tab, the Files tab among them.
 *
 * The picks are gathered across tabs (a person on Seen with and a tag on Tags) so moving between
 * the tabs must not drop them, and the Files tab is where they filter something, so it is the one
 * tab they must reach. Carried are the whole of those five fields, typed values too: a filter a
 * person put on the Files tab's bar is still in force when they come back to it through a card tab.
 */
export function carryPicks(href: string, url: URL): string {
	const carried = PICK_FIELDS.flatMap((field) =>
		url.searchParams.getAll(field).map((value) => [field, value] as const)
	);
	if (carried.length === 0) return href;
	const target = new URL(href, url);
	for (const field of PICK_FIELDS) target.searchParams.delete(field);
	for (const [field, value] of carried) target.searchParams.append(field, value);
	return spelled(target, target.searchParams);
}

/** A page's whole tab strip, each tab carrying the picks. See `carryPicks`. */
export function tabsCarryingPicks<Tab extends { href: string }>(
	tabs: readonly Tab[],
	url: URL
): Tab[] {
	return tabs.map((tab) => ({ ...tab, href: carryPicks(tab.href, url) }));
}

/**
 * How many files the page's Files tab holds while picks filter it.
 *
 * The record's own count (`asset_count`) is the unfiltered whole, wrong while a pick is in force.
 * The filtered total is asked of the same listing the Files tab reads, with the same two filters
 * joined the same way (`bothNarrowings`), one row, so the strip and the tab cannot disagree. Null
 * where nothing is picked: the record's own count is then right and costs nothing.
 *
 * For the four pages whose Files tab is the media grid over `/assets`. A collection's strip number
 * is its own listing's total, asked once more with the picks alone while words or a bar filter are
 * in force. Asking `/assets` would count the files carrying the collection's name instead.
 */
export async function narrowedFilesTotal(
	url: URL,
	own: Record<string, string>
): Promise<number | null> {
	const picks = picksOf(url);
	if (picks.length === 0) return null;
	const fromAddress: Record<string, string | string[]> = {};
	for (const one of picks) {
		const held = fromAddress[one.field];
		fromAddress[one.field] =
			held === undefined ? one.name : [...(Array.isArray(held) ? held : [held]), one.name];
	}
	const answer = await api.get<components['schemas']['AssetPageResponse']>('/assets', {
		query: { ...bothNarrowings(fromAddress, own), limit: 1 }
	});
	return answer.total;
}
