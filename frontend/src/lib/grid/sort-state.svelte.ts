import { readStored, writeStored } from '$lib/shell/remembered.svelte';
import type { IconName } from '$lib/design/icons';
import type { SortChoice } from '$lib/components/shell/screen-bar.svelte';

/* Which order the grid is in, kept in the browser so it is known before the first paint. */

const KEY = 'sift.grid.sort';

export const SORT_OPTIONS = [
	{ value: 'relevance', label: 'Closest match' },
	/* To a Smart Search's words or one file (`similarityChoice`); undescribed files sort last. */
	{ value: 'similarity', label: 'Similarity' },
	{ value: 'newest', label: 'Newest first' },
	{ value: 'oldest', label: 'Oldest first' },
	/*
	 * The last change to what the thing IS, by anyone; never-edited follows
	 * (`kernel/access/edited.py`).
	 */
	{ value: 'edited', label: 'Recently edited' },
	{ value: 'name_az', label: 'Name A-Z' },
	{ value: 'name_za', label: 'Name Z-A' },
	{ value: 'longest', label: 'Longest' },
	{ value: 'shortest', label: 'Shortest' },
	/* Bytes, said exactly; walls that count say so in their own words (`COUNTED_INSTEAD`). */
	{ value: 'largest', label: 'Largest file' },
	{ value: 'smallest', label: 'Smallest file' },
	/* Per viewer, answering only for the person asking. */
	{ value: 'viewed', label: 'Recently viewed' },
	{ value: 'most_viewed', label: 'Most viewed' },
	{ value: 'o_count', label: 'Highest O count' },
	{ value: 'favorite', label: 'Favorites first' },
	{ value: 'rating', label: 'Highest rated' },
	/* Offered only by Favorites (`WALL_ONLY_ORDERS`), listed so a stored order can be checked. */
	{ value: 'favorited', label: 'Recently favorited' },
	{ value: 'favorited_oldest', label: 'Oldest favorited' },
	/* A shuffle that HOLDS while you page, from a seed written into the address (`mintSeed`). */
	{ value: 'random', label: 'Random' }
] as const;

export const FAVORITED_ORDERS = ['favorited', 'favorited_oldest'] as const;
export const WALL_ONLY_ORDERS: ReadonlySet<string> = new Set(FAVORITED_ORDERS);

export const RANDOM = 'random';

/* A PRESS, not an order: the same shuffle asked again, an `action` row never remembered. */
export const RESHUFFLE = 'reshuffle';

export const RESHUFFLE_OPTION: SortChoice = {
	value: RESHUFFLE,
	label: 'Shuffle again',
	action: true
};

/* 2^31-1, the prime the server's permutation folds onto. */
export const SHUFFLE_SEED_LIMIT = 2147483647;

/** `Math.random` is enough: nothing is protected, the seed is in the address. */
export function mintSeed(): number {
	return Math.floor(Math.random() * SHUFFLE_SEED_LIMIT);
}

/* One glyph for every key above. */
const SORT_ICONS: Record<string, IconName> = {
	relevance: 'search',
	similarity: 'image_search',
	newest: 'clock_arrow_up',
	oldest: 'clock_arrow_down',
	edited: 'edit',
	name_az: 'sort_by_alpha',
	name_za: 'sort_by_alpha',
	longest: 'hourglass_arrow_up',
	shortest: 'hourglass_arrow_down',
	largest: 'data_usage',
	smallest: 'data_usage',
	largest_total: 'data_usage',
	smallest_total: 'data_usage',
	longest_total: 'hourglass_arrow_up',
	shortest_total: 'hourglass_arrow_down',
	viewed: 'history',
	most_viewed: 'visibility',
	o_count: 'water_drop',
	favorited: 'clock_arrow_up',
	favorited_oldest: 'clock_arrow_down',

	random: 'shuffle',
	reshuffle: 'shuffle',
	favorite: 'favorite',
	rating: 'star_rate',
	site: 'public',
	artist: 'person'
};

export function sortIcon(value: string): IconName | undefined {
	return SORT_ICONS[value];
}

const UNIVERSAL_KEYS = [
	'newest',
	'oldest',
	'edited',
	'name_az',
	'name_za',
	'largest',
	'smallest'
] as const;

/* Away from the file wall `largest` counts files under a thing. */
const COUNTED_INSTEAD: Record<string, string> = {
	largest: 'Most files',
	smallest: 'Fewest files'
};

function counted(key: string): { value: string; label: string } {
	const found = SORT_OPTIONS.find((option) => option.value === key);
	// Fires when a key above is renamed and this is missed.
	if (!found) throw new Error(`no sort option is spelled ${key}`);
	return { value: found.value, label: COUNTED_INSTEAD[found.value] ?? found.label };
}

const TOTAL_SORTS: readonly { value: string; label: string }[] = [
	{ value: 'largest_total', label: 'Largest in total' },
	{ value: 'smallest_total', label: 'Smallest in total' },
	{ value: 'longest_total', label: 'Longest in total' },
	{ value: 'shortest_total', label: 'Shortest in total' }
];

export const UNIVERSAL_SORTS: readonly { value: string; label: string }[] = [
	...UNIVERSAL_KEYS.map(counted),
	...TOTAL_SORTS
];

export const ENTITY_OPINION_SORTS: readonly { value: string; label: string }[] = [
	'favorite',
	'rating'
].map(counted);

/* Only the download queue orders by Site; "Site A-Z" because a bare noun is not an order. */
export const SITE_ORDER = { value: 'site', label: 'Site A-Z' } as const;

/* Only the Music wall; nobody credited sorts last (`SONG_SORT_ARTIST`). */
export const ARTIST_ORDER = { value: 'artist', label: 'Artist A-Z' } as const;

/* A wall of FOLDERS: the universal six; size and time are a second request. */
const FOLDER_KEYS = ['newest', 'oldest', 'name_az', 'name_za', 'largest', 'smallest'] as const;

export const FOLDER_ORDERS: readonly { value: string; label: string }[] = FOLDER_KEYS.map(counted);

export const FOLDERS_DEFAULT_SORT = 'name_az';

export const DEFAULT_SORT = 'newest';

export const RELEVANCE = 'relevance';

export const SIMILARITY = 'similarity';

/** The older spelling of search by meaning, kept for links already sent. */
export const RELEVANCE_BY_MEANING = SIMILARITY;

export type SimilarTo = 'words' | 'file' | null;

/** ONE LINE EACH at the menu's width (`NOTE_FITS`). */
export const SIMILAR_TO_WORDS = 'to the words';
export const SIMILAR_TO_FILE = 'to the file';
export const SIMILARITY_NEEDS = 'needs a Smart Search or a file';

export const NOTE_FITS = 32;

/** One function, so no two walls word the Similarity row differently. */
export function similarityChoice(
	to: SimilarTo,
	label = 'Similarity'
): { value: string; label: string; note: string; disabled?: boolean } {
	if (to === 'words') return { value: SIMILARITY, label, note: SIMILAR_TO_WORDS };
	if (to === 'file') return { value: SIMILARITY, label, note: SIMILAR_TO_FILE };
	return { value: SIMILARITY, label, note: SIMILARITY_NEEDS, disabled: true };
}

/* A `like:` names a file and any other term is words; words win, as on the server. */
const TERM = /\(*-?[a-z_]+:(?:"[^"]*"|\S+)|"[^"]*"|\S+/gi;
const FIELD_TERM = /^-?[a-z_]+:/i;
const JOINER = /^(?:OR|AND|NOT|-)$/;

export function similarToQuery(query: string): SimilarTo {
	const terms = (query.match(TERM) ?? []).map((term) => term.replace(/^[()]+|[()]+$/g, ''));
	if (terms.some((term) => term && !FIELD_TERM.test(term) && !JOINER.test(term))) return 'words';
	return terms.some((term) => /^like:\S/i.test(term)) ? 'file' : null;
}

const KNOWN: ReadonlySet<string> = new Set(SORT_OPTIONS.map((option) => option.value));

function remembered(): string {
	const stored = readStored(KEY);
	return stored && KNOWN.has(stored) ? stored : DEFAULT_SORT;
}

class SortState {
	value = $state(remembered());

	set(next: string): void {
		if (!KNOWN.has(next)) return;
		this.value = next;
		// Storage may refuse; the grid still sorts.
		writeStored(KEY, next);
	}
}

export const gridSort = new SortState();
