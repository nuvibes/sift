/*
 * Each search-list row's glyph and its word, in one table keyed by what the row is, so the two
 * cannot come apart (`search-kinds.test.ts` holds it to the server's fields). Facet glyphs come
 * from `facet-labels`; the word is singular for a field that names things.
 */
import type { IconName } from '$lib/design/icons';
import { QUERY_KIND, type Row } from '$lib/search/search.svelte';
import { facetIcon } from './facet-labels';

/** One kind of row: what it is called, and its glyph where no facet declares one. */
interface RowKind {
	name: string;
	icon?: IconName;
}

/** The kinds of row that are not a field of the query language. */
export const FILE_KIND = 'file';
export const WORDS_KIND = 'text';
export const MORE_KIND = 'more';

export const ROW_KINDS: Readonly<Record<string, RowKind>> = {
	/* A search typed and run: history, the clock. */
	[QUERY_KIND]: { name: 'Recent search', icon: 'history' },
	/* A file has no field, so without this it would wear the tag glyph. */
	[FILE_KIND]: { name: 'File', icon: 'videocam' },
	/* The one glyph on the list that names no kind of thing. */
	[WORDS_KIND]: { name: 'Words', icon: 'match_case' },
	/* Downwards, because that is where what it reveals appears. */
	[MORE_KIND]: { name: 'More', icon: 'expand_more' },

	people: { name: 'Person' },
	sites: { name: 'Site' },
	tags: { name: 'Tag' },
	collections: { name: 'Collection' },
	photo_sets: { name: 'Photo Set' },
	/* The glyph of the Songs column, read from the facet list. */
	songs: { name: 'Song' },
	in: { name: 'Folder' },
	music: { name: 'Music' },
	media: { name: 'Media' },
	filetype: { name: 'File type' },
	loops: { name: 'Loops' },
	rating: { name: 'Rating' },
	o_count: { name: 'O count' },
	fav: { name: 'Favorites' },
	sharing: { name: 'Sharing status' },
	added: { name: 'Added' },
	duration: { name: 'Duration' },
	resolution: { name: 'Resolution' },
	size: { name: 'File size', icon: 'hard_disk' },
	vcodec: { name: 'Video codec' },
	acodec: { name: 'Audio codec' },
	/* A title is a given name; a file name is what is on disk. */
	title: { name: 'Title', icon: 'titlecase' },
	filename: { name: 'File name', icon: 'article' },
	viewed: { name: 'View status' },
	orientation: { name: 'Orientation' },
	enriched: { name: 'Enriched by' },
	enrichment: { name: 'Asked a stash-box' },
	created: { name: 'Created by' },
	gender: { name: 'Gender' },
	hair: { name: 'Hair color' },
	eyes: { name: 'Eye color' },
	ethnicity: { name: 'Ethnicity' },
	nationality: { name: 'Nationality' },
	breasts: { name: 'Breast type' },
	/* The PMV-creator flag wears the same mark the person's page and card wear for it. */
	pmv: { name: 'PMV creator', icon: 'cinematic_blur' },
	pmv_creator: { name: 'PMV creator', icon: 'cinematic_blur' },
	height: { name: 'Height' },
	age: { name: 'Age' },
	released: { name: 'Released' },
	network: { name: 'Network' },
	/* The note of the Music column: the same thing, asked from one file. */
	same_music: { name: 'Same music', icon: 'music_note_2' },
	/* The glyph of the file menu's verb, so filter and verb are one. */
	like: { name: 'Similar to this', icon: 'image_search' },
	left_out: { name: 'Left out' },
	unnamed_face: { name: 'Face still unnamed' }
};

/** What a row drawn for a kind this table does not know wears: a filter, said as one. */
const UNKNOWN: Required<RowKind> = { name: 'Filter', icon: 'filter_alt' };

/** Which kind a row is: the key it is looked up by in `ROW_KINDS`. */
function rowKindKey(row: Row): string {
	if (row.kind === 'recent') return row.remembered.kind;
	if (row.kind === 'more') return MORE_KIND;
	if (row.kind === 'text') return WORDS_KIND;
	if (row.kind === 'match' && row.match.opens === FILE_KIND) return FILE_KIND;
	const field = row.kind === 'filter' ? row.filter.field : row.match.field;
	return field ?? '';
}

/** A kind's glyph and word, by its key. */
export function kindOf(key: string): Required<RowKind> {
	const known = ROW_KINDS[key];
	if (known === undefined) return UNKNOWN;
	return { name: known.name, icon: known.icon ?? facetIcon(key) ?? UNKNOWN.icon };
}

/** A row's glyph and word. */
export function rowKind(row: Row): Required<RowKind> {
	return kindOf(rowKindKey(row));
}
