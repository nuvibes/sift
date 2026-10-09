/* The cards picked on an entity page's tabs, as the filter on that page's files. Picks live in
 * the address as filter parameters (one per card, by name, AND-ed on the server) carried by every
 * tab, so the card tabs and the Files tab cannot disagree. A value with a minus, pipe or comma is
 * a typed filter, carried untouched and never shown as picked. */

import type { components } from '$lib/api/schema';
import { api } from '$lib/api/client';
import { bothNarrowings } from '$lib/components/shell/facet-labels';
import { fieldOf } from '$lib/components/shell/facet-labels';
import { kindOf, type RelatedKind } from '$lib/entity/related.svelte';

/** The query-language fields a card on an entity tab can stand for. */
type PickField = 'people' | 'tags' | 'sites' | 'collections' | 'photo_sets' | 'songs';

/* In tab order, so picks read back stably; `songs` is a Music tab's card. */
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

/** The field a card on this tab stands for; null for media walls and `sites_within`. */
export function pickFieldOf(showing: RelatedKind): PickField | null {
	if (showing === 'sites_within' || showing === 'tags_within') return null;
	const kind = kindOf(showing);
	if (!kind) return null;
	const field = fieldOf(kind);
	return (PICK_FIELDS as readonly string[]).includes(field) ? (field as PickField) : null;
}

/** One name as a filter value the parser reads back: quoted around commas, pipes and a minus. */
export function asFilterValue(name: string): string {
	const bare = name.replaceAll('"', '').trim();
	return /[,|]/.test(bare) || bare.startsWith('-') ? `"${bare}"` : bare;
}

/** The one name a filter value picks, or null for a typed filter. */
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

/** The address's filter on these fields, typed values included, as a listing takes it. */
export function narrowingOf(url: URL): Record<string, string[]> {
	const asked: Record<string, string[]> = {};
	for (const field of PICK_FIELDS) {
		const values = url.searchParams.getAll(field);
		if (values.length > 0) asked[field] = values;
	}
	return asked;
}

export function isPicked(picks: readonly Pick[], field: PickField, name: string): boolean {
	return picks.some((one) => one.field === field && one.name === name.trim());
}

function spelled(url: URL, params: URLSearchParams): string {
	const search = params.toString();
	return `${url.pathname}${search ? `?${search}` : ''}`;
}

/** The parameters with the named picks removed; typed values stay. */
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

/** A tab's address carrying the picks (and typed values on those fields) to every tab. */
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

/** The Files tab's count while picks filter it, from the same listing; null with no picks. */
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
