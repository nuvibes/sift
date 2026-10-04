/*
 * WHAT EACH ROW OF THE SEARCH LIST IS: its glyph and the word for it, in one table.
 *
 * The list under the top search box draws a glyph at the head of every row, so a bare word
 * offering a person, a tag and the Tags filter at once shows which each would become when picked.
 * A glyph on its own is a shape, though, and the ones for an audio codec, a view status or a
 * remembered search are shapes nobody is born knowing. So each glyph carries its kind's name, on
 * hover through the shared tooltip and to a screen reader as the glyph's own name.
 *
 * ONE TABLE, keyed by what the row IS, never a word written per row. The glyph and the word are
 * two answers to one question ("what kind of thing is this row"), and kept in two places they come
 * apart: a glyph added for a new field with no word beside it is a glyph that says nothing.
 *
 * Keyed by the TOKEN the server sends as a row's `field` (`filters.py`'s `Field`), plus the four
 * kinds of row that are no field: a remembered search, a file, the words themselves and Show more.
 * `search-kinds.test.ts` reads the server's field list and refuses one this table does not name.
 *
 * A facet's glyph is declared once, in `facet-labels`, and read from there: one picture per
 * dimension on the panel, the chip and this list. Only the fields that are no facet carry a glyph
 * here. Where Sift already has a mark for the thing, the field wears THAT mark rather than a second
 * one (a Photo Set, Loops, a folder, a file, the enriching wand), so the list and the screen a row
 * leads to cannot come to disagree about what a thing looks like.
 *
 * The word is the SINGULAR for a field that names things (a row offering Ada is a Person, not
 * People) and the filter's own label for the rest, the same words the list's filter rows draw.
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
	/* A file has no field (there is no `files:` filter), so without its own entry it would fall
	   through to the tag glyph and read as a tag that opens something else. */
	[FILE_KIND]: { name: 'File', icon: 'videocam' },
	/* The words as words. Its whole job is to be the row that is NOT a person or a tag, so it wears
	   the one glyph on the list that names no kind of thing. */
	[WORDS_KIND]: { name: 'Words', icon: 'match_case' },
	/* Downwards, because that is where what it reveals appears. */
	[MORE_KIND]: { name: 'More', icon: 'expand_more' },

	people: { name: 'Person' },
	sites: { name: 'Site' },
	tags: { name: 'Tag' },
	collections: { name: 'Collection' },
	photo_sets: { name: 'Photo Set' },
	/* A song, as a row offering one: the Songs column's own glyph, read from the facet list. */
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
	/* The two names a file has. The title is what somebody gave it and the file name is what is on
	   disk, so one wears the words glyph and the other the app's glyph for a file. */
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
	/* The files sharing a song with one file: the Music column's own note, because it is the same
	   thing asked from one file rather than by the song's name. */
	same_music: { name: 'Same music', icon: 'music_note_2' },
	/* The files similar to one file: the magnifier over a picture the file menu's Similar to this
	   row wears, so the filter and the verb are one glyph. */
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
