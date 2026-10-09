/*
 * Which family a card belongs to, which of the family's three swatches it wears, and the pictures
 * its ground is made of.
 *
 * The swatch follows the top file's colour (the server's `accent_hue`, an OKLCH hue in degrees):
 * the swatch whose hue is nearest. A file with no colour of its own, or none sent, wears the
 * middle one. The hues are the ones `app.css` writes as `--family-<family>-a|b|c`, and a test
 * holds the two together.
 */
import type { RecapCard } from '$lib/library/recaps.svelte';

export const FAMILIES = [
	'viewing',
	'people',
	'sites',
	'organizing',
	'theater',
	'downloads',
	'alongside'
] as const;
export type Family = (typeof FAMILIES)[number];
type Swatch = 'a' | 'b' | 'c';

/** Each family's three hues, in OKLCH degrees, in swatch order. */
export const HUES: Record<Family, readonly [number, number, number]> = {
	viewing: [245, 262, 280],
	people: [355, 12, 30],
	sites: [181, 196, 212],
	organizing: [62, 78, 95],
	theater: [286, 302, 320],
	downloads: [132, 148, 165],
	alongside: [225, 245, 265]
};

const KIND_FAMILY: Partial<Record<RecapCard['kind'], Family>> = {
	top_person: 'people',
	top_five: 'people',
	race: 'people',
	top_site: 'sites',
	top_tag: 'organizing',
	sift_did: 'organizing',
	rated: 'organizing',
	o: 'organizing',
	theater: 'theater',
	theater_files: 'theater',
	downloads: 'downloads',
	alongside: 'alongside'
};

/** A card's family by its kind: everything about viewing, the year's own cards and the cover
 *  included, is `viewing`. */
export function familyOf(kind: RecapCard['kind']): Family {
	return KIND_FAMILY[kind] ?? 'viewing';
}

/** The way round the circle from one hue to another, never more than half of it. */
function apart(one: number, other: number): number {
	const turn = Math.abs(one - other) % 360;
	return Math.min(turn, 360 - turn);
}

/** The swatch nearest the top file's hue; the middle one where there is none. */
export function swatchOf(family: Family, hue: number | null | undefined): Swatch {
	if (hue === null || hue === undefined || !Number.isFinite(hue)) return 'b';
	const hues = HUES[family];
	const near = hues.map((one) => apart(one, hue));
	return (['a', 'b', 'c'] as const)[near.indexOf(Math.min(...near))];
}

/** At most this many pictures make a card's ground: past it they are a blur of the same. */
export const GROUND_MOST = 6;

/** The FILES' pictures a deck shows on its cards, each once, in the deck's order: a deck's ground.
 *  Only files: a face behind a card that names somebody else would read as that person's card.
 *  Read off the cards the server drew for this reader, so the vault's rules are already kept. */
export function groundOf(cards: readonly RecapCard[]): string[] {
	const found = new Set<string>();
	const file = (cover: string | null | undefined) => {
		if (cover && cover.startsWith('/api/assets/')) found.add(cover);
	};
	for (const card of cards) {
		if (card.hidden) continue;
		file(card.cover);
		for (const row of card.rows ?? []) file(row.cover);
	}
	return [...found].slice(0, GROUND_MOST);
}
