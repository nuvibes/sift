/* Screen words for filter values where the query language's word is not one to read; shared
 * by the panel and the bar's chips, and following preferences without a reload. */

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

// Stash-boxes Sift has a word for, by the server's slug, spelled as each spells itself.
const BOX_NAMES: Record<string, string> = {
	stashdb: 'StashDB',
	pmvstash: 'PMVStash',
	fansdb: 'FansDB'
};

/** A box as a row of the Enriched by column; `EnrichmentMarks` says the same sentence. */
export function boxRowLabel(name: string): string {
	return `Stash-box: ${name}`;
}

const BOX_ROWS: Record<string, string> = Object.fromEntries(
	Object.entries(BOX_NAMES).map(([slug, name]) => [slug, boxRowLabel(name)])
);

const LABELS: Record<string, Record<string, string>> = {
	// The screen's words for the three kinds; the value stays the language's (`media:gif`).
	media: { image: 'Photos', video: 'Videos', gif: 'GIFs' },
	// Every state is spelled out, since a value also comes from a typed address.
	viewed: {
		yes: 'Viewed',
		/* `yes` stays the wide word, which Recently viewed is built on. */
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
	// `stash` is the union of the box rows; its word stays for a chip built from an address.
	enriched: {
		stash: 'A stash-box',
		faces: 'Sift: from a face',
		folder: 'Sift: from a folder name',
		/* Sift read the file's own name, which told it the Site, never who is in it. */
		filename: 'Sift: from a file name',
		/* Its own row: a name and a mark often disagree. */
		watermark: 'Sift: from a watermark',
		/* A file-name inference confirmed by two fields inside the file; worth checking. */
		metadata: 'Sift: from file metadata',
		/* AcoustID's name first, as a stash-box's row, because AcoustID said it and Sift asked. */
		acoustid: 'AcoustID: from the music',
		/* Never values of this facet: words for the Created by line (`madeBySaid`). */
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
		/* Entity walls count `none` too: both halves are worth looking at. */
		none: 'Not enriched',
		...BOX_ROWS
	},
	/** Who made the row; rows older than the column carry no word. */
	created: {
		sift: 'Sift',
		me: 'Me',
		/* Reviewing these is the reason to open this column after bringing Stash in. */
		stash_unattached: 'From Stash, attached to nothing',
		...BOX_NAMES
	},
	/** How recently a box answered, apart from who wrote; "Kept local" is the one decision. */
	enrichment: {
		local: 'Kept local',
		never: 'Never asked',
		today: 'Asked today',
		week: 'Asked this week',
		month: 'Asked this month',
		older: 'Asked longer ago'
	},
	/** Presence facets on entity walls, said whole; `linked` is retired (`LINKED_FACET`). */
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

// A stash-box's enum words (`BLONDE`), title-cased only per facet: a tag is a chosen name.
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

// Named by `$lib/people/countries`, so a facet value and a person's record read alike.
const NAMED_COUNTRIES = new Set(['nationality', 'country']);

/** Height bands under either spelling; stored in centimetres, read in the account's units. */
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

// Names the server sent for ids, so a chip drawn from the address reads a name; bounded.
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

// Only the author columns wear a glyph per value; each box Sift knows has its own.
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
	/* Not the `enriched:` glyphs: that column names which pass, this one only that a pass ran. */
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

// The ways Sift made a person, Site or tag: one row per task, in its page's own words.
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

// Who made a file, in the words and glyphs of the line that names makers (`madeLabel`).
const FILE_MAKERS: Readonly<Record<string, { readonly via: string; readonly act?: string }>> = {
	compress: { via: 'produced', act: 'compress' },
	edit: { via: 'produced', act: 'edit' },
	swap: { via: 'swap' },
	download: { via: 'download' },
	library: { via: 'library' }
};

// Which act a `produced` row came from; `facet-labels.test.ts` holds each glyph to its verb.
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

/** A row after a preposition: a leading article is lowercased ("Created by a stash-box"). */
export function afterWords(row: string): string {
	return row.replace(/^(An?|The) /, (article) => article.toLowerCase());
}

/** The readable name for a value, narrowest rule first, else the value untouched. */
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
	/* The words are `$lib/shell/measure`'s, so the column and the record agree. */
	if (HEIGHT_BANDS.has(facet)) return heightBand(value, appearance.units);
	if (TITLE_CASED.has(facet)) return titleCased(value);
	return value;
}

// "Never" for nought; an older cut's ranges still come from kept links.
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

// Duration bands as words; a gate holds this table to the server's `duration` column.
const DURATION_BANDS: Record<string, string> = {
	'0s..<1m': 'Under 1 minute',
	'1m..<3m': '1 to 3 minutes',
	'3m..<5m': '3 to 5 minutes',
	'5m..<15m': '5 to 15 minutes',
	'15m..<30m': '15 to 30 minutes',
	'30m..<60m': '30 to 60 minutes',
	'60m+': '60 minutes or more'
};

/* An older cut, still in kept links; `bandOrder` reads only the table above. */
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

// MB and GB though `size:` scales by 1024: the unit typed is the unit the row shows.
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

/** A banded dimension's values in the server's order, or null for a list, not a ladder. */
export function bandOrder(facet: string): readonly string[] | null {
	if (facet === 'duration') return Object.keys(DURATION_BANDS);
	if (facet === 'size') return Object.keys(SIZE_BANDS);
	return null;
}

// Columns of whole numbers (an age, an O count), listed ascending like a ladder.
const COUNTED_UP: ReadonlySet<string> = new Set(['age', 'o_count']);

/** Whether a column's rows are numbers to list in ascending order. See `COUNTED_UP`. */
export function countsUp(facet: string): boolean {
	return COUNTED_UP.has(facet);
}

/** A stored rating as this account's stars; `none` and `any` are their own answer. */
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
	/** What the values are ids of, so the chip can ask that page for a name. */
	names?: 'site' | 'tag';
	/** Still filtered by on the server but no longer a column: drawn only as a chip. */
	retired?: boolean;
}

// The file dimensions; the server refuses a token not on its own copy of the list.
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
	/* "View status": a heading that is one of the answers reads as a filter for it. */
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
	/* Retired: the Music column groups the same files; kept so old links still narrow. */
	{ key: 'music', label: 'Music', icon: 'music_note_2', retired: true },
	{ key: 'orientation', label: 'Orientation', icon: 'mobile_rotate' },
	/* Who wrote without a person; Enrich's glyph, as `auto_fix_high` is one of its rows'. */
	{ key: 'enriched', label: 'Enriched by', icon: 'backlight_low' },
	/* Who made the file, in the column every wall carries; rows wear each maker's mark. */
	{ key: 'created', label: 'Created by', icon: 'inventory_2' },
	/*
	 * A span (`span`, read by both the column and the fetch so nothing groups files by a date), in
	 * the chooser with the rest, and called "Added" as the query language calls it.
	 */
	{ key: 'added', label: 'Added', icon: 'calendar_clock', span: true },
	// Left out, not disabled: the server refuses it to anybody else.
	{ key: 'sharing', label: 'Sharing status', icon: 'group', admin: true },
	/*
	 * The file's dates and network, then the person's facets asked through the people on it. The
	 * `enrichment` rows (when asked) stay apart from `enriched` (who wrote), which would overlap.
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

/** Retired: `enriched` replaced it, but a saved `?linked=yes` must still filter. */
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
	/* One row per box that wrote to it, as boxes may disagree. */
	{ key: 'enriched', label: 'Enriched by', icon: 'backlight_low' },
	/* The nearest picture glyph, shared with a column never on one panel with this. */
	{ key: 'cover', label: 'Has a cover photo', icon: 'photo_library' },
	/* Which box made the row, apart from which described it since. */
	{ key: 'created', label: 'Created by', icon: 'inventory_2' },
	SHARING_FACET,
	/* Last and admin-only: settling one is an admin's to do. */
	{ key: 'disagrees', label: 'Stash-box disagreements', icon: 'data_info_alert', admin: true },
	// Last, and never drawn: the panel is built from the columns and this is not one.
	LINKED_FACET
];

const SITE_FACETS: readonly Facet[] = [
	/* "Network", not "Parent", which describes the data's shape. */
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
	/* The tag this one is filed under, named in the record's word. */
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

/* Only a collection has Whose: a photo set has no column for it. */
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
/* A song's own column is who it credits, answered with the artist's name. */
const SONG_FACETS: readonly Facet[] = [
	{ key: 'artists', label: 'Artists', icon: 'artist' },
	{ key: 'cover', label: 'Has a cover photo', icon: 'photo_library' },
	{ key: 'created', label: 'Created by', icon: 'inventory_2' },
	SHARING_FACET
];

/* The download queue's one column, its Site; the state is the tab strip. */
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

export function facetNames(subject: Subject, key: string): 'site' | 'tag' | undefined {
	return BY_SUBJECT[subject].find((one) => one.key === key)?.names;
}

/** Just the keys, retired ones in, so a saved link does not widen to the whole wall. */
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

// The file route reads a repeat as "and" with a pipe for "or"; the entity routes, as "or".
export function picksAreRepeated(subject: Subject): boolean {
	return subject !== 'asset';
}

// Whether a wall's words reach its counts as a name prefix; on files `q` is the query language.
export function wordsAreANamePrefix(subject: Subject): boolean {
	return subject !== 'asset' && subject !== 'download';
}

/** One answer for every facet route; an alias, so a dropped field is a build error. */
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

/** What a dimension is called, so both spellings (`hair`, `hair_color`) read the same. */
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

/** The facet parameters in an address for this noun; repeats stay repeats. */
export function facetParams(subject: Subject, params: URLSearchParams): Record<string, string[]> {
	const wanted: Record<string, string[]> = {};
	for (const key of facetKeys(subject)) {
		const held = params.getAll(key).filter((one) => one.trim() !== '');
		if (held.length > 0) wanted[key] = held;
	}
	return wanted;
}

// The field naming a kind of thing; the value is its name, so a shared name filters to both.
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

// Fields a file can hold several of; any other keeps the screen's value (`bothNarrowings`).
const MANY_PER_FILE: ReadonlySet<string> = new Set(Object.values(FIELD_OF));

/*
 * The address cannot remove a screen's own constraint, so both apply as AND. Repeated, never
 * comma-joined, so a minus or a pipe is never read as part of a name.
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

// A History line's files (`?filed=<site>~<source>~<day>[~<box>]`), decided on the server.

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

/** The chip's value, "<name> by Sift on <day>"; with no name known it says "a Site". */
export function filingLabel(filing: Filing, name: string | null): string {
	const who = name ?? FILING_WORDS[filing.parameter].unnamed;
	const when = filing.day ? ` on ${calendarDay(filing.day)}` : ' \u2014 no date recorded';
	return `${who} ${filingActor(filing)}${when}`;
}
