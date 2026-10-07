import { readStored, writeStored } from '$lib/shell/remembered.svelte';
import type { IconName } from '$lib/design/icons';
import type { SortChoice } from '$lib/components/shell/screen-bar.svelte';

/* Which order the grid is in, and where that choice is kept.
 *
 * In the browser, as the tile size is, so it is known before the first paint; one choice for the
 * whole application, since a preference for newest first holds on every wall. The keys are the
 * server's; an unknown stored value reads as the default, and the server refuses anything it does
 * not know.
 */

const KEY = 'sift.grid.sort';

/** The orders offered, in the order they are shown, with the label each carries on the control. */
export const SORT_OPTIONS = [
	/* Closest match: the order a screen with words in its query takes unless another is asked. */
	{ value: 'relevance', label: 'Closest match' },
	/* Closest to the words of a Smart Search or to one file; the menu says which, or why not
	 * (`similarityChoice`). A file not yet described sorts after every one that is. */
	{ value: 'similarity', label: 'Similarity' },
	{ value: 'newest', label: 'Newest first' },
	{ value: 'oldest', label: 'Oldest first' },
	/* The last change to what the thing IS (name, fields, cover, tags, stars), by a person or by
	   Sift, never a look or an arrival; never-edited follows. One key on every wall
	   (`kernel/access/edited.py`). */
	{ value: 'edited', label: 'Recently edited' },
	{ value: 'name_az', label: 'Name A-Z' },
	{ value: 'name_za', label: 'Name Z-A' },
	/* "Longest", not "Longest first"; the time orders keep "first" since "Newest" alone is a thing. */
	{ value: 'longest', label: 'Longest' },
	{ value: 'shortest', label: 'Shortest' },
	/*
	 * HOW MUCH ROOM A FILE TAKES, said exactly; the walls that order by a count say so with their
	 * own words (`COUNTED_INSTEAD`). Two truthful names, never one vague one for both.
	 */
	{ value: 'largest', label: 'Largest file' },
	{ value: 'smallest', label: 'Smallest file' },
	/*
	 * THE ORDERS ABOUT THIS ACCOUNT rather than the library, read from the per-viewer state, so
	 * they answer only for the person asking. `viewed` is the Recently viewed screen's order.
	 */
	{ value: 'viewed', label: 'Recently viewed' },
	{ value: 'most_viewed', label: 'Most viewed' },
	/* Also per viewer; everything never pressed sorts behind everything that was. */
	{ value: 'o_count', label: 'Highest O count' },
	/* This viewer's heart and stars. */
	{ value: 'favorite', label: 'Favorites first' },
	{ value: 'rating', label: 'Highest rated' },
	/* When the heart was pressed: offered only by the Favorites wall (`WALL_ONLY_ORDERS`), but
	   listed here so a remembered order or the address can be checked against it. */
	{ value: 'favorited', label: 'Recently favorited' },
	{ value: 'favorited_oldest', label: 'Oldest favorited' },
	/* A shuffle that HOLDS while you page (`_ORDER_TAILS['random']` on the server), chosen by a
	   seed minted on the press and written into the address (`mintSeed`, `RESHUFFLE`). */
	{ value: 'random', label: 'Random' }
] as const;

/** The orders a wall offers only when it declares them (`RowSource.adds`): the Favorites wall's two. */
export const FAVORITED_ORDERS = ['favorited', 'favorited_oldest'] as const;
export const WALL_ONLY_ORDERS: ReadonlySet<string> = new Set(FAVORITED_ORDERS);

/** The shuffled order, named rather than spelled at each call site. The server's own key. */
export const RANDOM = 'random';

/*
 * A PRESS, not an order: the one row on the sort menu that is asking for the same order again.
 *
 * The chooser refuses a re-selection, which is harmless for every order but a shuffle, where asking
 * again means a different draw. So while the grid is shuffled the menu carries this row, which is
 * not in `SORT_OPTIONS` and is never remembered. It is an `action` row (`common/Select.svelte`):
 * fired, never kept, so it can be pressed again and again.
 */
export const RESHUFFLE = 'reshuffle';

/** The reshuffle row, drawn under Random while that is the order showing. A press, never a value. */
export const RESHUFFLE_OPTION: SortChoice = {
	value: RESHUFFLE,
	label: 'Shuffle again',
	action: true
};

/*
 * The size of the space a seed is drawn from: 2^31-1, the prime the server's permutation folds
 * onto, so two different-looking addresses are never folded into one shuffle.
 */
export const SHUFFLE_SEED_LIMIT = 2147483647;

/**
 * A fresh shuffle to ask for. `Math.random` is enough: nothing is protected, the seed is in the
 * address, and it only has to differ from the last.
 */
export function mintSeed(): number {
	return Math.floor(Math.random() * SHUFFLE_SEED_LIMIT);
}

/*
 * The glyph each order wears on the menu, and there is one for every key above.
 *
 * A table, since `Select` owns the entries' shape (as `facetIcon` for dimensions). The two name
 * rows share the alphabet, the words saying which way; the time orders do not share, since an arrow
 * up or down is read immediately: a clock for arrival, an hourglass for running time.
 */
const SORT_ICONS: Record<string, IconName> = {
	relevance: 'search',
	/* The mark `Similar to this` wears, so the order and the strip of lookalikes read as one idea. */
	similarity: 'image_search',
	newest: 'clock_arrow_up',
	oldest: 'clock_arrow_down',
	/* History's pencil, so the order and the line that moved it wear one mark. */
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
	/* Time orders too: the arrow says which end, the words whose moment. */
	favorited: 'clock_arrow_up',
	favorited_oldest: 'clock_arrow_down',

	random: 'shuffle',
	/* The SAME order asked again, so the same glyph; the words carry the difference. */
	reshuffle: 'shuffle',
	favorite: 'favorite',
	rating: 'star_rate',
	/* The globe every Site wears. */
	site: 'public',
	/* The Music wall's Artists column mark. */
	artist: 'person'
};

/** The mark one order wears, or nothing for a key no list here declares. */
export function sortIcon(value: string): IconName | undefined {
	return SORT_ICONS[value];
}

/*
 * The orders that apply to EVERY kind of thing in the library, taken out of the list above by key
 * so the People wall reads the same strings as Browse.
 */
const UNIVERSAL_KEYS = [
	'newest',
	'oldest',
	'edited',
	'name_az',
	'name_za',
	'largest',
	'smallest'
] as const;

/*
 * The two keys that measure a DIFFERENT THING away from the file wall: there `largest` counts the
 * files under a thing, so a tag wall says "Most files", never "Largest file". Only these two.
 */
const COUNTED_INSTEAD: Record<string, string> = {
	largest: 'Most files',
	smallest: 'Fewest files'
};

/** One entry from the list above, in the words a wall of things says it in. */
function counted(key: string): { value: string; label: string } {
	const found = SORT_OPTIONS.find((option) => option.value === key);
	// Fires when a key above is renamed and this is missed, rather than leaving a silent hole.
	if (!found) throw new Error(`no sort option is spelled ${key}`);
	return { value: found.value, label: COUNTED_INSTEAD[found.value] ?? found.label };
}

/* The orders that SUM every file under a thing; the file grid has none, its `largest` is bytes. */
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

/* The heart and the stars, after the shared orders on every wall that keeps opinions. */
export const ENTITY_OPINION_SORTS: readonly { value: string; label: string }[] = [
	'favorite',
	'rating'
].map(counted);

/*
 * THE ORDER BY SITE, which only the download queue offers: elsewhere the Site is a filter or the
 * thing listed. Declared here with every other order's words and glyph, and not in `SORT_OPTIONS`,
 * which is the file grid's menu. "Site A-Z" because a bare noun is not an order.
 */
export const SITE_ORDER = { value: 'site', label: 'Site A-Z' } as const;

/*
 * THE ORDER BY ARTIST, which only the Music wall offers: by the first credited artist, crediting
 * nobody last (`SONG_SORT_ARTIST`); the server refuses it elsewhere.
 */
export const ARTIST_ORDER = { value: 'artist', label: 'Artist A-Z' } as const;

/*
 * The orders a wall of FOLDERS can be put in: the universal six, and what each means here.
 *
 * The tree carries names only, so size and time are a second request made when chosen. `Most
 * files` counts subfolders too; `Newest first` is the most recent arrival under the folder, never
 * a creation date the rows do not hold. Taken out of `SORT_OPTIONS` by key.
 */
const FOLDER_KEYS = ['newest', 'oldest', 'name_az', 'name_za', 'largest', 'smallest'] as const;

export const FOLDER_ORDERS: readonly { value: string; label: string }[] = FOLDER_KEYS.map(counted);

/** What a folder list is in until somebody says otherwise. Alphabetical, like a file manager. */
export const FOLDERS_DEFAULT_SORT = 'name_az';

export const DEFAULT_SORT = 'newest';

/** Closest match first: named once, since the grid decides when it applies and the server keys it. */
export const RELEVANCE = 'relevance';

/** Most similar first: the server's own key for the Similarity order. */
export const SIMILARITY = 'similarity';

/**
 * The older spelling of "search by meaning", as an order: kept because links already sent say it.
 * Nothing writes it, since how to search is its own parameter; it is `SIMILARITY`'s key, which is
 * why a Similarity request to a file sends `meaning=0` beside it.
 */
export const RELEVANCE_BY_MEANING = SIMILARITY;

/** What a Similarity order is close TO on a wall: the words of a Smart Search, or one file. */
export type SimilarTo = 'words' | 'file' | null;

/** The words under Similarity on the menu, saying what it is close to, or why it cannot be chosen.
 *  ONE LINE EACH at the menu's width (`NOTE_FITS`), or the last order falls under the scroll. */
export const SIMILAR_TO_WORDS = 'to the words';
export const SIMILAR_TO_FILE = 'to the file';
export const SIMILARITY_NEEDS = 'needs a Smart Search or a file';

/** The most letters a row's note may hold and stay on one line under its label in the Sort menu. */
export const NOTE_FITS = 32;

/**
 * The Similarity row as one wall draws it: free where there is something to be close to, dimmed
 * with its reason where not. One function, so no two walls say it differently.
 */
export function similarityChoice(
	to: SimilarTo,
	label = 'Similarity'
): { value: string; label: string; note: string; disabled?: boolean } {
	if (to === 'words') return { value: SIMILARITY, label, note: SIMILAR_TO_WORDS };
	if (to === 'file') return { value: SIMILARITY, label, note: SIMILAR_TO_FILE };
	return { value: SIMILARITY, label, note: SIMILARITY_NEEDS, disabled: true };
}

/*
 * What a stored query is close to, read off its text (a Theater cell): a `like:` names a file and
 * any non-field term is words, and words win, as on the server.
 */
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
	/** The chosen order, one of `SORT_OPTIONS`. */
	value = $state(remembered());

	set(next: string): void {
		if (!KNOWN.has(next)) return;
		this.value = next;
		// Storage may refuse; the grid still sorts and forgets by the next load.
		writeStored(KEY, next);
	}
}

export const gridSort = new SortState();
