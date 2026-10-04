/**
 * Where a row on the search list leads, when picking it goes somewhere rather than filtering the box.
 *
 * Only outside a token: inside `people:Ada` every row finishes the token, and one that navigated
 * would throw the query away. Keyed by the row's id, never by its name, which two people can share.
 * One entry per kind answers both following a row and remembering one, so the two cannot disagree:
 * a page by its id, a filter by the name the language matches (`tags:` resolves names to ids). A tag
 * has no page: what is under it is the library filtered to it.
 */

import type { Remembered, Row } from '$lib/search/search.svelte';

const PLACES: Record<string, { subject: 'id' | 'value'; page: (subject: string) => string }> = {
	/* A file completes to no token: it is a thing you open, not a set you filter to. */
	file: { subject: 'id', page: (one) => `/asset/${one}` },
	people: { subject: 'id', page: (one) => `/people/${one}` },
	sites: { subject: 'id', page: (one) => `/sites/${one}` },
	/* The older spelling, kept because remembered rows are stored under it. */
	platforms: { subject: 'id', page: (one) => `/sites/${one}` },
	collections: { subject: 'id', page: (one) => `/collections/${one}` },
	tags: { subject: 'value', page: (one) => `/browse?tags=${encodeURIComponent(one)}` },
	in: { subject: 'value', page: (one) => `/browse?in=${encodeURIComponent(one)}` },
	music: { subject: 'value', page: (one) => `/browse?music=${encodeURIComponent(one)}` }
};

/**
 * What a picked row is: its kind, what stands for it, and what it says. Null where the row is not
 * something the box can take you to, which includes every row while the caret is inside `token`.
 */
export function placeOf(row: Row, token: string | null | undefined): Remembered | null {
	if (row.kind !== 'match') return null;
	if (token != null) return null;
	const { field, id, value, opens } = row.match;
	// A file has no field (see `PLACES.file`) so its kind is what it OPENS.
	const kind = opens === 'file' ? 'file' : field;
	if (kind == null) return null;
	const place = PLACES[kind];
	if (place === undefined) return null;
	const subject = place.subject === 'id' ? id : value;
	if (!subject) return null;
	return { kind, subject, label: value };
}

/** Where a remembered row goes, by the same registry. */
export function pageFor(remembered: Remembered): string | null {
	return PLACES[remembered.kind]?.page(remembered.subject) ?? null;
}

/** Whether pressing this row leaves the page, which a row says so nobody has to press to find out. */
export function leaves(row: Row, token: string | null | undefined): boolean {
	if (row.kind === 'recent') return pageFor(row.remembered) !== null;
	return placeOf(row, token) !== null;
}
