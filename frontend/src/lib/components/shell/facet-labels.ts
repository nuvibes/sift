/*
 * What a filter VALUE is called on screen, where the word the query language uses is not a word to
 * read.
 *
 * A row writes the language's own value, so it counts and selects one set; only the word drawn on
 * it changes. `viewed` (whose `none` would read as an empty file), `rating` (stored out of ten,
 * drawn at the account's scale, so two stored values can read alike, as on a tile) and height
 * bands (drawn in the account's measurement system) are the cases. Here, not in a component,
 * because the panel and the bar's chips draw the same values. The preferences are runes, so the
 * words follow a switch in Settings without a reload.
 */

import { SvelteMap } from 'svelte/reactivity';
import type { ApiPath } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { appearance } from '$lib/theme/appearance.svelte';
import { countryName } from '$lib/people/countries';
import { heightBand } from '$lib/shell/measure';
import { calendarDay } from '$lib/shell/when';
import { ratingScale } from '$lib/library/rating.svelte';
import type { IconName } from '$lib/design/icons';
import type { EntityKind } from '$lib/entity/related.svelte';

/*
 * THE STASH-BOXES SIFT HAS A WORD FOR, spelled the way each service spells itself.
 *
 * Keyed by the slug the server derives from the box's address, so it is the same word on every
 * install, never what somebody typed when adding the box. One table for both `enriched:` and
 * `created:`, which ask the same boxes two questions.
 */
const BOX_NAMES: Record<string, string> = {
	stashdb: 'StashDB',
	pmvstash: 'PMVStash',
	fansdb: 'FansDB'
};

/*
 * The same boxes, said as a row of the Enriched by column: "Stash-box: FansDB".
 *
 * That column's rows read `<who>: <how>`; Created by keeps the plain names, since each of its rows
 * names only a maker. Built from the table above so a box cannot appear in one column only, and a
 * function because `EnrichmentMarks` says the same sentence.
 */
export function boxRowLabel(name: string): string {
	return `Stash-box: ${name}`;
}

const BOX_ROWS: Record<string, string> = Object.fromEntries(
	Object.entries(BOX_NAMES).map(([slug, name]) => [slug, boxRowLabel(name)])
);

const LABELS: Record<string, Record<string, string>> = {
	// The screen's words for the three kinds; the value stays the language's (`media:gif`).
	media: { image: 'Photos', video: 'Videos', gif: 'GIFs' },
	// Two-way, because most of a library has no notion of being finished. Every state the language
	// takes is spelled out, since a value also arrives from an address a person typed or followed.
	viewed: {
		yes: 'Viewed',
		/* Opened and not part-way, the column's middle row. `yes` stays the wide word, because
		   Recently viewed is built on it. */
		done: 'Viewed',
		none: 'Not viewed',
		finished: 'Finished',
		started: 'Started',
		/* Said as the act, since it is the list the Continue watching wall is made of. */
		continue: 'Continue watching'
	},
	// The tile's own two words, capitalised as on its badge.
	sharing: { shared: 'Shared', restricted: 'Restricted' },
	orientation: { landscape: 'Landscape', portrait: 'Portrait', square: 'Square' },
	// Whole facts on the rows, as `viewed` says them, because "yes" under a heading is read twice.
	fav: { yes: 'Favorite', no: 'Not a favorite' },
	loops: { any: 'Has Loops', none: 'No Loops' },
	// Said whole, for the reason the Loops row above is.
	songs: { any: 'Has a song', none: 'No song' },
	tags: { any: 'Has tags', none: 'No tags' },
	people: { any: 'Has people', none: 'No people' },
	sites: { any: 'Has a Site', none: 'No Site' },
	collections: { any: 'In a collection', none: 'In no collection' },
	photo_sets: { any: 'In a Photo Set', none: 'In no Photo Set' },
	// What Sift could not make for a file, in the words the rest of Sift names each product by.
	left_out: {
		thumbnails: 'Thumbnails',
		previews: 'Hover previews',
		sprites: 'Scrubbing strips',
		fingerprints: 'Fingerprints',
		faces: 'Faces',
		meaning: 'Smart Search',
		watermarks: 'Watermarks'
	},
	// The authors, said `<who>: <how>` so the column has one grammar. `stash` is the union of the
	// box rows, so it is not counted on the walls; its word stays for a chip built from an address.
	enriched: {
		stash: 'A stash-box',
		faces: 'Sift: from a face',
		folder: 'Sift: from a folder name',
		/* Sift read the file's own name, which told it the Site, never who is in it. */
		filename: 'Sift: from a file name',
		/* Sift read the picture for a Site's mark. Its own row because a name and a mark often
		   disagree, in both directions. */
		watermark: 'Sift: from a watermark',
		/* The same pass as the file-name row, resting on the name and two fields inside the file
		   (Artist, ImageDescription) agreeing on a username's number: an inference worth checking.
		   "file metadata" because any file can land here. */
		metadata: 'Sift: from file metadata',
		/* AcoustID's name first, as a stash-box's row, because AcoustID said it and Sift asked. */
		acoustid: 'AcoustID: from the music',
		/* NEVER VALUES OF THIS FACET: these passes create a row and file nothing onto one. They are
		   here because the table also answers "what does Sift call this pass" for the Created by
		   line, for every word `created_by_via` holds (`enrich.svelte.ts`'s `madeBySaid`). */
		download: 'Sift: from a download',
		/* A row (a file, a person, a Site, a Username) that arrived from another install. */
		swap: 'Sift: from a swap',
		/* A file Sift added from a library folder, as its History says (see `FILE_MAKERS`). */
		library: 'Sift: from a library folder',
		mirror: 'Sift: from a mirror folder',
		produced: 'Sift: from a file it made',
		archive: 'Sift: from an archive',
		shoot: 'Sift: from a shoot',
		/* A song made from what AcoustID answered; never a value of the facet. */
		music_lookup: 'Sift: from AcoustID',
		/* A person made from a facial fingerprints file or a folder of pictures; never a value. */
		facial_fingerprints: 'Sift: from facial fingerprints',
		/* What a Stash library imported: the library's own record, copied, so not `stash`. */
		stash_library: 'Sift: from a Stash library',
		/* Imported whole and attached to no file there, listed apart for one look over them. */
		stash_unattached: 'Sift: from a Stash library, attached to nothing there',
		/* The People, Sites and Tags walls count `none` too: an entity is or is not enriched, and
		   both halves are worth looking at. The presence facet's two words, deliberately. */
		none: 'Not enriched',
		...BOX_ROWS
	},
	/*
	 * Who made the row: a box, Sift on its own, or the account asking. `created:sift` is a stored
	 * answer, not the absence of a box; rows older than the column carry no word and are in no row.
	 */
	created: {
		sift: 'Sift',
		me: 'Me',
		/* Reviewing these is the reason to open this column after bringing Stash in. */
		stash_unattached: 'From Stash, attached to nothing',
		...BOX_NAMES
	},
	/*
	 * Whole facts on the rows. The words say asking, because this column counts when a box was
	 * last asked and Enriched by counts who wrote, so the two never hold numbers that conflict.
	 * "Kept local" first: the one row that is a decision, counting the whole rule.
	 */
	enrichment: {
		local: 'Kept local',
		never: 'Never asked',
		today: 'Asked today',
		week: 'Asked this week',
		month: 'Asked this month',
		older: 'Asked longer ago'
	},
	/*
	 * The presence facets on the entity walls, which answer `yes` and `no`, said as whole facts
	 * because "no" means something different under each. `linked` is retired (`LINKED_FACET`), so
	 * its words are read only on a chip built from an address.
	 */
	linked: { yes: 'Enriched by a stash-box', no: 'Not enriched' },
	cover: { yes: 'Has a cover photo', no: 'No cover photo' },
	/* "None" because the column's heading carries the whole phrase. */
	disagrees: { yes: 'Has disagreements', no: 'None' },
	/* Who MAKES the edits. Both spellings: `pmv_creator` is the record's key, `pmv` is typed. */
	pmv_creator: { yes: 'PMV creator', no: 'Not a PMV creator' },
	pmv: { yes: 'PMV creator', no: 'Not a PMV creator' },
	usernames: { yes: 'Has usernames', no: 'No usernames' },
	// A shelf somebody else made is here because it was SHARED, which is the fact worth reading.
	mine: { yes: 'Mine', no: 'Shared with me' }
};

/*
 * The words a stash-box sends, which are enum words rather than words to read (`BLONDE`).
 *
 * Title-cased here once for the panel, the chips and the tooltip, and only per facet: a tag, a
 * person or a folder is a name somebody chose.
 */
const TITLE_CASED = new Set([
	'gender',
	'hair',
	'hair_color',
	'eyes',
	'eye_color',
	'ethnicity',
	'breasts',
	'breast_type',
	'category'
]);

/*
 * An ISO country code, named by `$lib/people/countries` rather than `Intl.DisplayNames` (its head says
 * why), so a facet value and a person's record read identically.
 */
const NAMED_COUNTRIES = new Set(['nationality', 'country']);

/** The facets whose values are BANDS OF HEIGHT, under either of the dimension's two spellings.
 *  What is stored is centimetres; what is read follows the account. See `$lib/shell/measure`. */
const HEIGHT_BANDS = new Set(['height', 'height_cm']);

/** One enum word as a phrase. `BREAST_AUGMENTED` is two words to read and one word to store. */
function titleCased(word: string): string {
	return word
		.toLowerCase()
		.split(/[\s_]+/)
		.filter(Boolean)
		.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
		.join(' ');
}

/*
 * THE NAME THE SERVER SENT FOR AN ID, KEPT SO THE CHIP CAN READ IT.
 *
 * The chip is drawn from the address, which holds only the id, so without this a tag picked on the
 * People wall reads as a ULID. Bounded, oldest out first. Reactive, so a name arriving later
 * redraws the chip; one never counted is asked for by the bar (`facetNames`).
 */
const NAMED_BY_THE_SERVER = new SvelteMap<string, string>();
const REMEMBER_AT_MOST = 2000;

function nameKey(facet: string, value: string): string {
	/* A space, which no facet KEY contains, so the first one is always the join. */
	return `${facet} ${value}`;
}

/** Whether a name for this value has been kept, so the bar knows whether to ask for one. */
export function facetNameKnown(facet: string, value: string): boolean {
	return NAMED_BY_THE_SERVER.has(nameKey(facet, value));
}

/** Keep the names one column answered with, for whatever draws those values elsewhere. */
export function rememberFacetNames(facet: string, values: readonly FacetValue[]): void {
	for (const one of values) {
		if (!one.label) continue;
		const key = nameKey(facet, one.value);
		NAMED_BY_THE_SERVER.delete(key);
		NAMED_BY_THE_SERVER.set(key, one.label);
	}
	while (NAMED_BY_THE_SERVER.size > REMEMBER_AT_MOST) {
		const oldest = NAMED_BY_THE_SERVER.keys().next().value;
		if (oldest === undefined) break;
		NAMED_BY_THE_SERVER.delete(oldest);
	}
}

/*
 * The mark a value wears, where a word is not enough to tell the authors apart at a glance.
 * Only Enriched by and Created by, whose rows are authors; each box Sift knows wears its own glyph.
 */
const BOX_ICONS: Record<string, IconName> = {
	stashdb: 'inventory_2',
	pmvstash: 'movie',
	fansdb: 'person_celebrate'
};

const VALUE_ICONS: Record<string, Record<string, IconName>> = {
	enriched: {
		stash: 'inventory_2',
		faces: 'familiar_face_and_zone',
		facial_fingerprints: 'familiar_face_and_zone',
		folder: 'folder_supervised',
		filename: 'document_scanner',
		watermark: 'position_bottom_right',
		/* What a file SAYS about itself: the record panes' glyph. */
		metadata: 'quick_reference',
		/* A piece of music: the glyph the Music section and page wear. */
		acoustid: 'music_note_2',
		music_lookup: 'music_note_2',
		/* The passes the facet never shows, for the Created by line (see the labels above). */
		download: 'download',
		swap: 'swap_horiz',
		library: 'folder',
		mirror: 'content_copy',
		produced: 'auto_fix_high',
		archive: 'inbox',
		shoot: 'photo_library',
		...BOX_ICONS
	},
	/* Not the `enriched:` pass glyphs: that column names WHICH pass, this one only that it was one. */
	created: {
		sift: 'auto_awesome',
		me: 'account_circle',
		/* A broken chain: on nothing, which is what the row is for. */
		stash_unattached: 'link_off',
		...BOX_ICONS
	}
};

/** The columns whose values wear a glyph each. Read by the test that keeps it to authors. */
export const VALUE_GLYPH_FACETS: readonly string[] = Object.keys(VALUE_ICONS);

/** The glyph for one value of one dimension, or nothing where the word carries it. */
export function facetValueIcon(facet: string, value: string): IconName | undefined {
	const made = facet === 'created' ? FILE_MAKERS[value] : undefined;
	if (made) return madeIcon(made.via, made.act);
	if (facet === 'created' && SIFT_MADE_BY.has(value)) return madeIcon(value);
	return VALUE_ICONS[facet]?.[value];
}

/*
 * HOW SIFT MADE A PERSON, A SITE OR A TAG, as the Created by column on those walls counts it: one
 * row per task (`created_by_via`), each in the words the thing's own page uses ("Created by Sift,
 * from facial fingerprints" is the row "Sift: from facial fingerprints"). `sift` stays the row of
 * what Sift made before the task was recorded. `stash` is Sift acting on a stash-box's answer,
 * which the Enriched by column words as the box.
 */
const SIFT_MADE_BY: ReadonlySet<string> = new Set([
	'folder',
	'filename',
	'metadata',
	'watermark',
	'mirror',
	'faces',
	'stash',
	'produced',
	'archive',
	'shoot',
	'stash_library',
	'music_lookup',
	'facial_fingerprints'
]);
const SIFT_FROM_A_STASH_BOX_ANSWER = 'Sift: from a stash-box answer';

/*
 * WHO MADE A FILE, as the Created by column on a wall of files counts it (`created:`). Each wears
 * the words and glyph the Created by line uses for that maker (`madeLabel`, `madeIcon`), so the
 * two cannot drift. No word is also one of the other `created` values.
 */
const FILE_MAKERS: Readonly<Record<string, { readonly via: string; readonly act?: string }>> = {
	compress: { via: 'produced', act: 'compress' },
	edit: { via: 'produced', act: 'edit' },
	swap: { via: 'swap' },
	download: { via: 'download' },
	library: { via: 'library' }
};

/*
 * WHICH ACT made a row the `produced` pass made, keyed on `tags.created_by_act`.
 *
 * Each act wears its verb's glyph from a file's menu and History's word for it, since `produced`
 * alone would say two acts one way. A row with no act stored keeps the pass's general row. Every editor
 * operation is one act. `facet-labels.test.ts` holds each glyph to its verb's.
 */
export const MADE_ACTS: Readonly<
	Record<string, { readonly icon: IconName; readonly label: string }>
> = {
	compress: { icon: 'compress', label: 'Sift: from a file it compressed' },
	edit: { icon: 'design_services', label: 'Sift: from a file it edited' }
};

/** The glyph for the pass that made a row, the act's own where the act is stored. */
export function madeIcon(via: string, act?: string | null): IconName | undefined {
	return (
		(via === 'produced' && act ? MADE_ACTS[act]?.icon : undefined) ??
		facetValueIcon('enriched', via)
	);
}

/** The Enriched by column's row for the pass that made a row, the act's own where it is stored. */
export function madeLabel(via: string, act?: string | null): string {
	return (
		(via === 'produced' && act ? MADE_ACTS[act]?.label : undefined) ??
		facetValueLabel('enriched', via)
	);
}

/** One stash-box's own glyph, by slug, or nothing for a box Sift has no word for. */
export function boxIcon(box: string | null | undefined): IconName | undefined {
	return box ? BOX_ICONS[box] : undefined;
}

/**
 * A row read after a preposition: a row that is a noun phrase ("A stash-box") keeps its capital
 * only at the start of a line, so "Created by a stash-box" and "Enriched by a stash-box" read as
 * sentences. One rule for every line that puts a row after words of its own.
 */
export function afterWords(row: string): string {
	return row.replace(/^(An?|The) /, (article) => article.toLowerCase());
}

/**
 * The readable name for one value of one dimension, or the value itself where it needs none.
 *
 * Narrowest first: a spelled-out word, a name the server sent for an id, the facet kind's rule,
 * then the value untouched, so something odd in a library is seen rather than hidden.
 */
export function facetValueLabel(facet: string, value: string): string {
	const made = facet === 'created' ? FILE_MAKERS[value] : undefined;
	if (made) return madeLabel(made.via, made.act);
	if (facet === 'created' && SIFT_MADE_BY.has(value))
		return value === 'stash' ? SIFT_FROM_A_STASH_BOX_ANSWER : madeLabel(value);
	if (facet === 'rating') return ratingLabel(value);
	if (facet === 'o_count') return oCountLabel(value);
	if (facet === 'duration') return durationBandLabel(value);
	if (facet === 'size') return sizeBandLabel(value);
	const said = LABELS[facet]?.[value];
	if (said !== undefined) return said;
	const named = NAMED_BY_THE_SERVER.get(nameKey(facet, value));
	if (named !== undefined) return named;
	if (NAMED_COUNTRIES.has(facet)) return countryName(value);
	/* A height band in the account's system; the words are `$lib/shell/measure`'s, so the column and
	   the person's record cannot disagree about the same centimetres. */
	if (HEIGHT_BANDS.has(facet)) return heightBand(value, appearance.units);
	if (TITLE_CASED.has(facet)) return titleCased(value);
	return value;
}

/*
 * An O count as the panel says it: the stored count is the filter, "3 times" is read, and "Never"
 * for nought. An older cut's ranges (`o_count:2..4`) still arrive from kept links.
 */
const O_COUNT_RANGES: Record<string, string> = {
	'2..4': '2 to 4 times',
	'5..9': '5 to 9 times',
	'10+': '10 or more times'
};

function oCountLabel(value: string): string {
	if (value === '0') return 'Never';
	if (value === '1') return 'Once';
	if (/^\d+$/.test(value)) return `${value} times`;
	return O_COUNT_RANGES[value] ?? value;
}

/*
 * A duration band as the panel says it: the stored half-open range is the filter, the words are
 * read. The bands are the server's `duration` column, and a gate holds this table to it. Each
 * band reads as the stretch it holds.
 */
const DURATION_BANDS: Record<string, string> = {
	'0s..<1m': 'Under 1 minute',
	'1m..<3m': '1 to 3 minutes',
	'3m..<5m': '3 to 5 minutes',
	'5m..<15m': '5 to 15 minutes',
	'15m..<30m': '15 to 30 minutes',
	'30m..<60m': '30 to 60 minutes',
	'60m+': '60 minutes or more'
};

/* An older cut, still kept in links, so a chip reads as words. `bandOrder` reads only the table above. */
const DURATION_BEFORE: Record<string, string> = {
	'0s..<30s': 'Under 30 seconds',
	'30s..<60s': '30 seconds to 1 minute',
	'60s..<3m': '1 to 3 minutes',
	'5m..<10m': '5 to 10 minutes',
	'10m..<20m': '10 to 20 minutes',
	'20m+': 'Over 20 minutes'
};

function durationBandLabel(value: string): string {
	return DURATION_BANDS[value] ?? DURATION_BEFORE[value] ?? value;
}

/*
 * A file-size band as the panel says it, arranged as the duration bands are.
 *
 * MB and GB, though `size:` scales by 1024 (`_SIZE_UNITS`): the unit somebody types is the unit
 * the row shows, because the row teaches the filter, and the app writes sizes this way everywhere.
 */
const SIZE_BANDS: Record<string, string> = {
	'0..<1mb': 'Under 1 MB',
	'1mb..<10mb': '1 to 10 MB',
	'10mb..<100mb': '10 to 100 MB',
	'100mb..<1gb': '100 MB to 1 GB',
	'1gb..<5gb': '1 to 5 GB',
	'5gb+': '5 GB or more'
};

/* An older cut of the column, for a chip built from a filter kept under one. */
const SIZE_BEFORE: Record<string, string> = {
	'0..<10mb': 'Under 10 MB',
	'1gb+': 'Over 1 GB'
};

function sizeBandLabel(value: string): string {
	return SIZE_BANDS[value] ?? SIZE_BEFORE[value] ?? value;
}

/*
 * A BANDED dimension's values, in the order the server cut them, or nothing, for a dimension
 * whose values are a list rather than a ladder.
 *
 * Ordered by count, a ladder reads twice and the cut at five hides its ends. The tables above are
 * the ladder (a record literal keeps its written order), read a second way rather than copied.
 */
export function bandOrder(facet: string): readonly string[] | null {
	if (facet === 'duration') return Object.keys(DURATION_BANDS);
	if (facet === 'size') return Object.keys(SIZE_BANDS);
	return null;
}

/*
 * The columns whose values are whole NUMBERS listed one per row (an age, an O count), read in
 * ascending order like a ladder rather than by how many files each holds.
 */
const COUNTED_UP: ReadonlySet<string> = new Set(['age', 'o_count']);

/** Whether a column's rows are numbers to list in ascending order. See `COUNTED_UP`. */
export function countsUp(facet: string): boolean {
	return COUNTED_UP.has(facet);
}

/** A stored rating as the number of stars this account sees, or the value untouched when it is
 *  not a number: `rating:none` and `rating:any` are words, and are their own answer. */
function ratingLabel(value: string): string {
	const stored = Number(value);
	if (!Number.isInteger(stored)) return value;
	const stars = ratingScale.shown(stored);
	return stars === null ? value : `${stars} ${stars === 1 ? 'star' : 'stars'}`;
}

/** One dimension a column can show: its token, what it is called, and the mark it wears. */
export interface Facet {
	/** The query language's own word for it, which is also the parameter a pick writes. */
	key: string;
	label: string;
	/** The glyph on the chooser's row and on the chip. The same one the search dropdown draws. */
	icon: IconName;
	/** A range rather than a set of values to tick, so the panel draws a calendar. */
	span?: boolean;
	/** Only an admin may ask about it, so it is left out rather than drawn and refused. */
	admin?: boolean;
	/**
	 * The kind of thing this facet's values are the ids OF, where they are ids: the chip asks that
	 * thing's own page for its name when the panel has not handed one over.
	 */
	names?: 'site' | 'tag';
	/**
	 * A word the server still filters by that no panel offers as a column any more: read from an
	 * address and drawn as a chip, never in the chooser. A flag, as `admin` is, so a facet exists
	 * in one place.
	 */
	retired?: boolean;
}

/*
 * The dimensions a column can show, and what each is called on screen.
 *
 * The token is the filter a click writes, so a row counts and selects one set; the server refuses
 * a name not on its own copy of the list. The panel, the chip and the search dropdown wear one
 * glyph each from here (`search-kinds` reads `facetIcon`).
 */
export const FACETS = [
	{ key: 'media', label: 'Media', icon: 'camera_roll' },
	{ key: 'tags', label: 'Tags', icon: 'shoppingmode' },
	{ key: 'people', label: 'People', icon: 'person' },
	{ key: 'in', label: 'Folder', icon: 'folder' },
	{ key: 'rating', label: 'Rating', icon: 'star' },
	/* Whether this account hearted the file: the heart's own glyph, as on the Favorites wall. */
	{ key: 'fav', label: 'Favorites', icon: 'favorite' },
	/* The O counter, one row per count, in ascending order (see `countsUp`). */
	{ key: 'o_count', label: 'O count', icon: 'water_drop' },
	/* "View status", not "Viewed": a heading that is one of the answers reads as a filter for it.
	   The query language uses the same name. */
	{ key: 'viewed', label: 'View status', icon: 'undereye' },
	/* Whether a Loop has been marked in the file: the Loops screen's own glyph. */
	{ key: 'loops', label: 'Loops', icon: 'all_inclusive' },
	{ key: 'resolution', label: 'Resolution', icon: '4k' },
	{ key: 'duration', label: 'Duration', icon: 'schedule' },
	/* Bands, because no two files share a byte count, as for Duration, Resolution and Height. */
	{ key: 'size', label: 'File size', icon: 'hard_disk' },
	{ key: 'sites', label: 'Sites', icon: 'public' },
	{ key: 'collections', label: 'Collections', icon: 'box' },
	/* The pictures that arrived together, beside the other way a library is grouped. */
	{ key: 'photo_sets', label: 'Photo Sets', icon: 'photo_library' },
	/* The song a file carries: the Music page's own glyph and word. */
	{ key: 'songs', label: 'Music', icon: 'music_note_2' },
	{ key: 'filetype', label: 'File type', icon: 'file_png' },
	{ key: 'vcodec', label: 'Video codec', icon: 'av1' },
	{ key: 'acodec', label: 'Audio codec', icon: 'audio_file' },
	/* Retired: the Music column above groups the same files by the same names. Still a word an
	   address may carry, so a link written with it keeps narrowing. */
	{ key: 'music', label: 'Music', icon: 'music_note_2', retired: true },
	{ key: 'orientation', label: 'Orientation', icon: 'mobile_rotate' },
	/*
	 * Who wrote to the file without a person doing it; each row wears its author's mark
	 * (`facetValueIcon`). The column wears Enrich's glyph: `auto_fix_high` is one of its rows'
	 * marks and `auto_awesome` is search by meaning's on the same panel.
	 */
	{ key: 'enriched', label: 'Enriched by', icon: 'backlight_low' },
	/*
	 * Who made the file. The same column and glyph every wall carries for its makers, so one idea
	 * reads one way; its rows wear each maker's mark (`FILE_MAKERS`).
	 */
	{ key: 'created', label: 'Created by', icon: 'inventory_2' },
	/*
	 * A span (`span`, read by both the column and the fetch so nothing groups files by a date), in
	 * the chooser with the rest, and called "Added" as the query language calls it.
	 */
	{ key: 'added', label: 'Added', icon: 'calendar_clock', span: true },
	// Admin-only and left out rather than disabled: the server refuses it to anybody else, as it
	// does `sharing:` in the query language.
	{ key: 'sharing', label: 'Sharing status', icon: 'group', admin: true },
	/*
	 * The file's own dates and network, then the person's eight, asked of the file through the
	 * people on it, each with the label and glyph of its cousin in `PERSON_FACETS`. Their tokens
	 * are the search language's (`hair:`); the record's (`hair_color`) reach the same label.
	 *
	 * `enrichment` says when a box was last asked, every file in exactly one row; `enriched` says who
	 * wrote, a file under every pass. Folded together the rows would partly overlap, so they stay
	 * two. "Kept local" is a row because a file never sent is never asked, which is the distinction
	 * this column is opened to make.
	 */
	{ key: 'enrichment', label: 'Asked a stash-box', icon: 'backlight_low' },
	/* What Sift could not make for the file, as the Importing pane counts it. */
	{ key: 'left_out', label: 'Left out', icon: 'hide_image' },
	{ key: 'released', label: 'Released', icon: 'event' },
	/* No `produced` column: almost no file has a production date. */
	{ key: 'network', label: 'Network', icon: 'hub' },
	{ key: 'gender', label: 'Gender', icon: 'wc' },
	{ key: 'hair', label: 'Hair color', icon: 'face_3' },
	{ key: 'eyes', label: 'Eye color', icon: 'visibility' },
	{ key: 'ethnicity', label: 'Ethnicity', icon: 'diversity_2' },
	{ key: 'nationality', label: 'Nationality', icon: 'flag' },
	{ key: 'breasts', label: 'Breast type', icon: 'eyeglasses_3' },
	{ key: 'height', label: 'Height', icon: 'people_size_increase' },
	{ key: 'age', label: 'Age', icon: 'cake' }
] as const;

/*
 * The noun's facets, the same columns in the same order wherever a wall of it is drawn.
 *
 * Ordered by use, not the alphabet: a panel opens on the first five (`FacetPanel.options`). The
 * presence facets at the end ask whether a row has something, a fact about the row no record
 * field declares.
 *
 * `linked` is the column `enriched` replaced, kept as a retired word because the server still takes
 * it and a saved `?linked=yes` must filter. It is not mapped onto `enriched`: a box Sift has no
 * word for has no slug, so the mapping would be a silent subset. Declared once for the three walls.
 */
const LINKED_FACET: Facet = {
	key: 'linked',
	label: 'Stash-box link',
	icon: 'link',
	retired: true
};

/* Admin-only, on all five walls whose rows can be shared, placed before the disagreements. */
const SHARING_FACET: Facet = {
	key: 'sharing',
	label: 'Sharing status',
	icon: 'group',
	admin: true
};

const PERSON_FACETS: readonly Facet[] = [
	{ key: 'gender', label: 'Gender', icon: 'wc' },
	{ key: 'hair_color', label: 'Hair color', icon: 'face_3' },
	{ key: 'eye_color', label: 'Eye color', icon: 'visibility' },
	{ key: 'ethnicity', label: 'Ethnicity', icon: 'diversity_2' },
	// Labelled Nationality rather than Country: it is the person's own, not where a file came from.
	{ key: 'country', label: 'Nationality', icon: 'flag' },
	{ key: 'breast_type', label: 'Breast type', icon: 'eyeglasses_3' },
	{ key: 'height_cm', label: 'Height', icon: 'people_size_increase' },
	// Worked out from the birth date by the server, in bands, because an exact age is not a set.
	{ key: 'age', label: 'Age', icon: 'cake' },
	/* "Career start", since "Career" over a list of years reads as how long one lasted. */
	{ key: 'career_start_year', label: 'Career start', icon: 'work_history' },
	/* The glyph of the mark beside their name, so the column, the chip and the badge are one. */
	{ key: 'pmv_creator', label: 'PMV creator', icon: 'cinematic_blur' },
	{ key: 'tags', label: 'Tags', icon: 'shoppingmode', names: 'tag' },
	/* One row per box that wrote to this row, each with its glyph, since boxes may disagree.
	   `linked` is kept only as an address word (`LINKED_FACET`). */
	{ key: 'enriched', label: 'Enriched by', icon: 'backlight_low' },
	/* The nearest picture glyph; the Files wall's Photo Sets glyph, never on one panel with this. */
	{ key: 'cover', label: 'Has a cover photo', icon: 'photo_library' },
	/* Which box MADE the row, apart from which described it since: one many boxes know may have
	   been typed in by hand. */
	{ key: 'created', label: 'Created by', icon: 'inventory_2' },
	SHARING_FACET,
	/* Last and admin-only on all three walls: settling one is an admin's to do. The History tab
	   mark's glyph. */
	{ key: 'disagrees', label: 'Stash-box disagreements', icon: 'data_info_alert', admin: true },
	// Last, and never drawn: the panel is built from the columns and this is not one.
	LINKED_FACET
];

const SITE_FACETS: readonly Facet[] = [
	/* No `kind` column: almost no record carries one. "Network", not "Parent", which describes
	   the data's shape. */
	{ key: 'parent', label: 'Network', icon: 'hub', names: 'site' },
	{ key: 'tags', label: 'Tags', icon: 'shoppingmode', names: 'tag' },
	/* As on the People wall. */
	{ key: 'enriched', label: 'Enriched by', icon: 'backlight_low' },
	{ key: 'usernames', label: 'Usernames', icon: 'alternate_email' },
	{ key: 'cover', label: 'Has a cover photo', icon: 'photo_library' },
	/* As on the People wall. */
	{ key: 'created', label: 'Created by', icon: 'inventory_2' },
	SHARING_FACET,
	/* As on the People wall. */
	{ key: 'disagrees', label: 'Stash-box disagreements', icon: 'data_info_alert', admin: true },
	// Last, and never drawn: the panel is built from the columns and this is not one.
	LINKED_FACET
];

const TAG_FACETS: readonly Facet[] = [
	{ key: 'category', label: 'Category', icon: 'category' },
	/* The tag this one is filed under: the twin of the Sites wall's Network, in the record's word. */
	{ key: 'parent', label: 'Part of', icon: 'shoppingmode', names: 'tag' },
	/* As on the People wall. */
	{ key: 'enriched', label: 'Enriched by', icon: 'backlight_low' },
	{ key: 'cover', label: 'Has a cover photo', icon: 'photo_library' },
	{ key: 'created', label: 'Created by', icon: 'inventory_2' },
	SHARING_FACET,
	/* As on the People wall. */
	{ key: 'disagrees', label: 'Stash-box disagreements', icon: 'data_info_alert', admin: true },
	// Last, and never drawn: the panel is built from the columns and this is not one.
	LINKED_FACET
];

/* A photo set has no owner column, so only a collection has Whose; otherwise the same columns in
   the same words as the other walls. */
const COLLECTION_FACETS: readonly Facet[] = [
	{ key: 'mine', label: 'Whose', icon: 'person' },
	{ key: 'tags', label: 'Tags', icon: 'shoppingmode', names: 'tag' },
	{ key: 'cover', label: 'Has a cover photo', icon: 'photo_library' },
	{ key: 'created', label: 'Created by', icon: 'inventory_2' },
	SHARING_FACET
];
const PHOTO_SET_FACETS: readonly Facet[] = [
	{ key: 'tags', label: 'Tags', icon: 'shoppingmode', names: 'tag' },
	{ key: 'cover', label: 'Has a cover photo', icon: 'photo_library' },
	{ key: 'created', label: 'Created by', icon: 'inventory_2' },
	SHARING_FACET
];
/* A song carries no tags of its own; its one column of its own is who it credits, answered with
   the artist's name as the Sites wall's Network is. */
const SONG_FACETS: readonly Facet[] = [
	{ key: 'artists', label: 'Artists', icon: 'artist' },
	{ key: 'cover', label: 'Has a cover photo', icon: 'photo_library' },
	{ key: 'created', label: 'Created by', icon: 'inventory_2' },
	SHARING_FACET
];

/*
 * The download queue's one column: which Site a download came from, keyed `site` as its address
 * already is. The state is the screen's tab strip, so it is not also a column.
 */
const DOWNLOAD_FACETS: readonly Facet[] = [
	{ key: 'site', label: 'Site', icon: 'public', admin: true }
];

/** Which noun a wall is showing, which is what decides the panel's columns. */
export type Subject =
	'asset' | 'person' | 'site' | 'tag' | 'collection' | 'photo_set' | 'song' | 'download';

const BY_SUBJECT: Record<Subject, readonly Facet[]> = {
	asset: FACETS,
	person: PERSON_FACETS,
	site: SITE_FACETS,
	tag: TAG_FACETS,
	collection: COLLECTION_FACETS,
	photo_set: PHOTO_SET_FACETS,
	song: SONG_FACETS,
	download: DOWNLOAD_FACETS
};

/** Every COLUMN this noun has, in the fixed order its panel draws them, without the retired. */
export function facetsFor(subject: Subject): readonly Facet[] {
	return BY_SUBJECT[subject].filter((one) => !one.retired);
}

/**
 * Just the keys, for the bar deciding which parameters in the address are this wall's filters.
 * The retired ones are in, unlike `facetsFor`, so a saved link does not widen to the whole wall.
 */
export function facetNames(subject: Subject, key: string): 'site' | 'tag' | undefined {
	return BY_SUBJECT[subject].find((one) => one.key === key)?.names;
}

export function facetKeys(subject: Subject): readonly string[] {
	return BY_SUBJECT[subject].map((one) => one.key);
}

/* Where a noun's counts come from: each list route, under the filtering the wall is paged with. */
export function facetRoute(subject: Subject): ApiPath {
	if (subject === 'asset') return '/assets/facets';
	if (subject === 'person') return '/people/facets';
	if (subject === 'site') return '/sites/facets';
	if (subject === 'tag') return '/tags/facets';
	if (subject === 'collection') return '/collections/facets';
	if (subject === 'download') return '/downloads/facets';
	if (subject === 'song') return '/songs/facets';
	return '/photo-sets/facets';
}

/*
 * HOW A MULTI-VALUE PICK IS SPELLED, WHICH IS NOT THE SAME ON BOTH SIDES.
 *
 * The file route reads a repeated parameter as "and" with a pipe for "or"; the entity routes read
 * a repeat as "or" and know no pipe. A fact about the servers, written once.
 */
export function picksAreRepeated(subject: Subject): boolean {
	return subject !== 'asset';
}

/*
 * Whether a wall's words reach its facet counts as `prefix` matched anywhere in a name, which is
 * how the five walls of things read the words their own box writes. The file route reads `q` as
 * the query language, so on a wall of files the words go as they are.
 */
export function wordsAreANamePrefix(subject: Subject): boolean {
	return subject !== 'asset' && subject !== 'download';
}

/*
 * The one answer every wall's facet route gives, declared once on the kernel's wire. An alias over
 * the generated type, so a field the server drops is a build error (`check_one_server_type`).
 */
export type FacetValue = components['schemas']['FacetValue'];

export type FacetCounts = components['schemas']['FacetCounts'];

/** Every facet, across every noun, for the two lookups that are given a key and nothing else. */
const EVERY_FACET: readonly Facet[] = [
	...FACETS,
	...PERSON_FACETS,
	...SITE_FACETS,
	...TAG_FACETS,
	...COLLECTION_FACETS,
	...DOWNLOAD_FACETS
];

/*
 * What a dimension is CALLED on screen, across every noun, so both spellings (`hair` typed,
 * `hair_color` on the record) reach the same words.
 */
export function facetLabel(key: string, subject?: Subject): string {
	return declared(key, subject)?.label ?? key;
}

/** The glyph a dimension wears, wherever it is drawn. Nothing for a key no noun declares. */
export function facetIcon(key: string, subject?: Subject): IconName | undefined {
	return declared(key, subject)?.icon;
}

/* The wall's own entry first: `parent` is a Site's Network and a tag's Part of. */
function declared(key: string, subject?: Subject): Facet | undefined {
	const own = subject ? BY_SUBJECT[subject].find((one) => one.key === key) : undefined;
	return own ?? EVERY_FACET.find((one) => one.key === key);
}

/**
 * The facet parameters in an address, for the noun on the wall. Anything else (an offset, an
 * anchor) is no filter; repeated keys stay repeats.
 */
export function facetParams(subject: Subject, params: URLSearchParams): Record<string, string[]> {
	const wanted: Record<string, string[]> = {};
	for (const key of facetKeys(subject)) {
		const held = params.getAll(key).filter((one) => one.trim() !== '');
		if (held.length > 0) wanted[key] = held;
	}
	return wanted;
}

/*
 * Which field of the query language names one kind of thing, on a wall of files.
 *
 * A thing reached from another page lands on its own page with the origin as an ordinary filter
 * chip. The value is the name, which the server resolves for whoever asks; a name two things share
 * filters to both, the wider and visible direction.
 */
const FIELD_OF: Record<EntityKind, string> = {
	person: 'people',
	tag: 'tags',
	site: 'sites',
	collection: 'collections',
	photo_set: 'photo_sets',
	song: 'songs'
};

/** The field that names this kind of thing on a wall of files. */
export function fieldOf(kind: EntityKind): string {
	return FIELD_OF[kind];
}

/*
 * The fields a file can carry SEVERAL of, built from the table above. Every other field is one
 * value per file, so two filters on it keep the screen's value (`bothNarrowings`).
 */
const MANY_PER_FILE: ReadonlySet<string> = new Set(Object.values(FIELD_OF));

/*
 * TWO FILTERS ON ONE WALL, WHERE BOTH OF THEM APPLY.
 *
 * A screen's own constraint (a person's page is that person) must not be REMOVED by the address,
 * and AND gives that: the set narrows, never changes. Repeated, never comma-joined, because a comma
 * joins texts and would turn a minus or a pipe into part of a name (`parse_modal` reads each value
 * whole). An identical value on both sides collapses, since Browse passes the address down.
 */
export function bothNarrowings(
	fromAddress: Record<string, string | string[]>,
	own: Record<string, string>
): Record<string, string | string[]> {
	const asked: Record<string, string | string[]> = { ...fromAddress };
	for (const [key, value] of Object.entries(own)) {
		const held = asked[key];
		if (held === undefined || !MANY_PER_FILE.has(key)) {
			asked[key] = value;
			continue;
		}
		const both = [value, ...(Array.isArray(held) ? held : [held])];
		const kept = both.filter((one, at) => both.indexOf(one) === at);
		asked[key] = kept.length === 1 ? value : kept;
	}
	return asked;
}

/*
 * ONE HISTORY LINE'S FILES, as the chip on the bar says them.
 *
 * A History count opens the wall at `?filed=<site>~<source>~<day>[~<box>]` (or `tagged`, `named`),
 * which `constraints.Filing` decides on the server; here it is only said. A value this cannot read
 * is still a filter in force, so it is drawn as the address holds it.
 */

/** The three parameters, in the order their chips are drawn. */
export const FILING_PARAMETERS = ['filed', 'tagged', 'named'] as const;
export type FilingParameter = (typeof FILING_PARAMETERS)[number];

/** One line's group, read back off the address. */
export interface Filing {
	parameter: FilingParameter;
	subject: string;
	/** Null where somebody did it by hand: the line with no source. */
	source: string | null;
	/** `YYYY-MM-DD`, the UTC day the History counts by; null on the undated line. */
	day: string | null;
	/** Which stash-box, on a stash-box's line that names one. */
	box: string | null;
}

/** The address value as the group it names, or null where it is not one. */
export function readFiling(parameter: FilingParameter, value: string): Filing | null {
	const [subject, source, day, ...rest] = value.split('~');
	if (!subject || source === undefined || day === undefined) return null;
	if (day && !/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
	return {
		parameter,
		subject,
		source: source || null,
		day: day || null,
		/* Everything after the third separator: a box's name may hold one. */
		box: rest.length ? rest.join('~') : null
	};
}

/** What each parameter says before the name, and what it calls a thing it cannot name. */
const FILING_WORDS: Record<FilingParameter, { verb: string; unnamed: string }> = {
	filed: { verb: 'filed under', unnamed: 'a Site' },
	tagged: { verb: 'tagged', unnamed: 'a tag' },
	named: { verb: 'named as', unnamed: 'somebody' }
};

/** The label on the chip's lead, which is its dimension: "filed under", "tagged", "named as". */
export function filingField(parameter: FilingParameter): string {
	return FILING_WORDS[parameter].verb;
}

/* Who did it, in the History's words, as `sentences.filed_under` attributes them. */
function filingActor(filing: Filing): string {
	if (filing.source === null) return 'by hand';
	if (filing.source === 'stash_box') return `by ${filing.box ?? 'a stash-box'}`;
	return 'by Sift';
}

/* The day as every date is drawn (`calendarDay`), year and all. It is the server's calendar day,
 * with no zone to convert. */

/**
 * The chip's value: "Instagram by Sift on Sep 12, 2026". `name` is null until known or where the
 * viewer may not be told it, which says "a Site".
 */
export function filingLabel(filing: Filing, name: string | null): string {
	const who = name ?? FILING_WORDS[filing.parameter].unnamed;
	const when = filing.day ? ` on ${calendarDay(filing.day)}` : ' \u2014 no date recorded';
	return `${who} ${filingActor(filing)}${when}`;
}
