import { afterEach, describe, expect, it } from 'vitest';

import { appearance } from '$lib/theme/appearance.svelte';

import {
	boxIcon,
	facetIcon,
	facetKeys,
	facetLabel,
	facetParams,
	facetRoute,
	facetsFor,
	facetValueIcon,
	bothNarrowings,
	fieldOf,
	facetValueLabel,
	filingField,
	filingLabel,
	picksAreRepeated,
	readFiling,
	rememberFacetNames,
	VALUE_GLYPH_FACETS
} from './facet-labels';

/* The unit system is an account's preference and this module reads it. Put back after every test,
   or a file that runs in one process would leave the next one reading feet. */
afterEach(() => {
	appearance.units = 'metric';
});

/*
 * ONE FACET IS ONE THING EVERYWHERE.
 *
 * The same dimension is drawn in four places (the panel's column, the chip on the bar, the row in
 * the search dropdown and the tooltip on a kept filter) and this module is what makes those four
 * agree. What is worth pinning is therefore not "the function returns a string" but the properties
 * that break when a second copy of a rule appears somewhere:
 *
 *  - a facet with TWO spellings reads the same under both, because one of them is what somebody
 *    types at the Files wall and the other is what the person's own record calls it;
 *  - a stash-box's enum word is made readable HERE rather than at each drawing;
 *  - a name that somebody CHOSE (a tag, a folder) is never rewritten;
 *  - and every noun's columns come in the fixed order that noun declares, because the panel's shape
 *    is a property of the noun rather than of the page it is drawn on.
 */

describe('a facet is called one thing, under either spelling', () => {
	it('reads the same whichever of its two names is used', () => {
		/*
		 * `hair` is the search token and `hair_color` is the record's field. Two labels for those
		 * would be two facets on two walls.
		 */
		expect(facetLabel('hair')).toBe('Hair color');
		expect(facetLabel('hair_color')).toBe(facetLabel('hair'));
		expect(facetLabel('eyes')).toBe(facetLabel('eye_color'));
		expect(facetLabel('breasts')).toBe(facetLabel('breast_type'));
		expect(facetLabel('height')).toBe(facetLabel('height_cm'));
		expect(facetLabel('nationality')).toBe(facetLabel('country'));
	});

	it('wears the same glyph under either spelling', () => {
		expect(facetIcon('hair')).toBe(facetIcon('hair_color'));
		expect(facetIcon('nationality')).toBe(facetIcon('country'));
		expect(facetIcon('network')).toBe(facetIcon('parent'));
	});

	it("calls a person's country Nationality, which is whose it is rather than where a file is from", () => {
		expect(facetLabel('country')).toBe('Nationality');
		expect(facetLabel('parent')).toBe('Network');
	});

	it('hands back a key no noun declares, rather than inventing words for it', () => {
		expect(facetLabel('knitting')).toBe('knitting');
		expect(facetIcon('knitting')).toBeUndefined();
	});
});

describe('what a value reads as', () => {
	it('title-cases the enum word a stash-box sends', () => {
		expect(facetValueLabel('hair_color', 'BLONDE')).toBe('Blonde');
		expect(facetValueLabel('hair', 'BLONDE')).toBe('Blonde');
		expect(facetValueLabel('breast_type', 'BREAST_AUGMENTED')).toBe('Breast Augmented');
		expect(facetValueLabel('category', 'SCENE')).toBe('Scene');
	});

	it('leaves a name somebody CHOSE exactly as it is', () => {
		/* A tag, a person and a folder are values too. Title-casing every value would rewrite those
		   on screen, which is why the rule is named per facet rather than applied to all of them. */
		expect(facetValueLabel('tags', 'beach at sunset')).toBe('beach at sunset');
		expect(facetValueLabel('in', 'Sift Downloads')).toBe('Sift Downloads');
	});

	it('names a country from the one table that names them', () => {
		expect(facetValueLabel('country', 'US')).toBe('United States');
		expect(facetValueLabel('nationality', 'GB')).toBe('United Kingdom');
	});

	it('hands back a country code nothing claims, so a wrong one can be seen', () => {
		expect(facetValueLabel('country', 'ZZ')).toBe('ZZ');
	});

	it('puts the unit on a height band and leaves an age alone', () => {
		expect(facetValueLabel('height_cm', '160-169')).toBe('160-169 cm');
		expect(facetValueLabel('height', '200+')).toBe('200+ cm');
		expect(facetValueLabel('height', '<150')).toBe('<150 cm');
		// Nothing to add: the number already reads as an age, and "27 years" is longer for no gain.
		expect(facetValueLabel('age', '27')).toBe('27');
		expect(facetValueLabel('age', '25-29')).toBe('25-29');
	});

	it('reads a height band in the system this account chose, under either spelling', () => {
		/* The row still WRITES the stored band (`160-169` is what the query language takes) and
		   only the word on it follows the preference. Read here rather than passed in, the same way
		   the rating scale is, so the panel and the chips cannot come to disagree; the words and the
		   arithmetic are `$lib/shell/measure`'s and are pinned there. */
		appearance.units = 'imperial';
		expect(facetValueLabel('height_cm', '160-169')).toBe('5 ft 3 in to 5 ft 6 in');
		expect(facetValueLabel('height', '200+')).toBe('6 ft 7 in and over');
		expect(facetValueLabel('height', '<150')).toBe('4 ft 11 in and under');
		appearance.units = 'metric';
		expect(facetValueLabel('height', '160-169')).toBe('160-169 cm');
	});

	it('says the whole fact on a presence row, in that facet s own words', () => {
		/* A column headed "Linked to a stash-box" listing "yes" and "no" makes somebody read the
		   heading and the value together to get one fact. */
		expect(facetValueLabel('linked', 'yes')).toBe('Enriched by a stash-box');
		expect(facetValueLabel('linked', 'no')).toBe('Not enriched');
		// The second word is not the facet's own: the column is headed with the whole phrase and
		// these two rows sit under it, so repeating it would be the heading said three times.
		expect(facetValueLabel('disagrees', 'yes')).toBe('Has disagreements');
		expect(facetValueLabel('disagrees', 'no')).toBe('None');
		expect(facetValueLabel('cover', 'yes')).toBe('Has a cover photo');
		expect(facetValueLabel('cover', 'no')).toBe('No cover photo');
		expect(facetValueLabel('usernames', 'yes')).toBe('Has usernames');
		expect(facetValueLabel('mine', 'yes')).toBe('Mine');
		expect(facetValueLabel('mine', 'no')).toBe('Shared with me');
	});

	it('names the three kinds of file with the nouns the screen uses, on a row and on a chip', () => {
		/* The Media column and the chip it writes both read through here, so "gif" on a row and
		   "media: gif" on the bar were one missing entry. The value stays the language's word. */
		expect(facetValueLabel('media', 'image')).toBe('Photos');
		expect(facetValueLabel('media', 'video')).toBe('Videos');
		expect(facetValueLabel('media', 'gif')).toBe('GIFs');
	});

	it('reads each of the seven duration bands as words while the value stays the filter', () => {
		/*
		 * The duration column is seven bands, and this is the readable half of that one
		 * declaration: a band the server cuts with no word here would fall through and draw
		 * `1m..<3m` among English.
		 */
		expect(facetValueLabel('duration', '0s..<1m')).toBe('Under 1 minute');
		expect(facetValueLabel('duration', '1m..<3m')).toBe('1 to 3 minutes');
		expect(facetValueLabel('duration', '3m..<5m')).toBe('3 to 5 minutes');
		expect(facetValueLabel('duration', '5m..<15m')).toBe('5 to 15 minutes');
		expect(facetValueLabel('duration', '15m..<30m')).toBe('15 to 30 minutes');
		expect(facetValueLabel('duration', '30m..<60m')).toBe('30 to 60 minutes');
		expect(facetValueLabel('duration', '60m+')).toBe('60 minutes or more');
	});

	it('reads a band of an older cut as the stretch it holds, and an unknown one as stored', () => {
		/* A filter kept under an older cut still holds `0s..<30s`. It parses and returns the same files
		   it always did, so its chip says the stretch it holds in that stretch's own words, never the
		   name of a new band that means something else. A range nothing names is drawn as stored. */
		expect(facetValueLabel('duration', '0s..<30s')).toBe('Under 30 seconds');
		expect(facetValueLabel('duration', '20m+')).toBe('Over 20 minutes');
		expect(facetValueLabel('size', '1gb+')).toBe('Over 1 GB');
		expect(facetValueLabel('duration', '0s..<60s')).toBe('0s..<60s');
		expect(facetValueLabel('duration', '5m..<20m')).toBe('5m..<20m');
	});

	it('reads an O count as a number of times, and a kept range as its words', () => {
		expect(facetValueLabel('o_count', '0')).toBe('Never');
		expect(facetValueLabel('o_count', '1')).toBe('Once');
		expect(facetValueLabel('o_count', '7')).toBe('7 times');
		expect(facetValueLabel('o_count', '2..4')).toBe('2 to 4 times');
	});

	it('reads a file-size band as words while the value stays the filter', () => {
		/* The whole point of a banded facet: the row WRITES the query language and READS as English.
		   The bands are half-open, so the file sitting exactly on a cut belongs to the band above it
		   and no row can publish a number that clicking it does not return.

		   The numbers behind these words are binary (`size:` scales by 1024), which is why the
		   words say MB and GB rather than inventing a unit the filter does not take. */
		expect(facetValueLabel('size', '0..<1mb')).toBe('Under 1 MB');
		expect(facetValueLabel('size', '1mb..<10mb')).toBe('1 to 10 MB');
		expect(facetValueLabel('size', '10mb..<100mb')).toBe('10 to 100 MB');
		expect(facetValueLabel('size', '100mb..<1gb')).toBe('100 MB to 1 GB');
		expect(facetValueLabel('size', '1gb..<5gb')).toBe('1 to 5 GB');
		expect(facetValueLabel('size', '5gb+')).toBe('5 GB or more');
	});

	it('hands back a size the column did not cut, rather than inventing words for it', () => {
		// The same rule every other value follows: a value nothing claims is drawn as it is stored,
		// which is what lets somebody see that something odd arrived rather than hiding it.
		expect(facetValueLabel('size', '500mb+')).toBe('500mb+');
	});

	it('gives each stash-box Sift knows its own glyph, and nothing to one it does not', () => {
		expect(boxIcon('stashdb')).toBe('inventory_2');
		expect(boxIcon('pmvstash')).toBe('movie');
		expect(boxIcon('fansdb')).toBe('person_celebrate');
		// The columns' rows wear the same glyph as the marks.
		for (const box of ['stashdb', 'pmvstash', 'fansdb']) {
			expect(facetValueIcon('enriched', box)).toBe(boxIcon(box));
			expect(facetValueIcon('created', box)).toBe(boxIcon(box));
		}
		expect(boxIcon('someone-elses-box')).toBeUndefined();
		expect(boxIcon(null)).toBeUndefined();
		expect(boxIcon(undefined)).toBeUndefined();
	});

	it('names every stash-box it has a word for, under both dimensions', () => {
		/* One table of names read by `enriched:` and `created:`, so a box added to one is a box
		   added to both. The service's own capitalisation, not whatever somebody typed into the
		   box when they added it: a row is a word in the query language before it is a name. */
		for (const box of ['stashdb', 'pmvstash', 'fansdb']) {
			/*
			 * The Enriched by row says what kind of thing wrote as well as which one, because its
			 * neighbours name Sift and what it read; a Created by row is the bare name, because
			 * every row in that column names its maker and nothing else. One table underneath both,
			 * so they differ only by the prefix.
			 */
			expect(facetValueLabel('enriched', box)).toBe(
				`Stash-box: ${facetValueLabel('created', box)}`
			);
			expect(facetValueLabel('enriched', box)).not.toBe(box);
		}
		// The union keeps its own words and is not a box.
		expect(facetValueLabel('enriched', 'stash')).toBe('A stash-box');
		expect(boxIcon('stash')).toBeUndefined();
	});

	it('names the two makers that are not a stash-box, under Created by', () => {
		/*
		 * Catalog version 41 records which of the three made a row, so Sift and an account are two
		 * stored answers rather than one leftover ("nobody configured here made it", which would be
		 * every person somebody typed in as well as every one a pass invented, and not one fact). A
		 * row made before that column existed appears under neither.
		 */
		expect(facetValueLabel('created', 'sift')).toBe('Sift');
		expect(facetValueLabel('created', 'me')).toBe('Me');
		// Their own marks: `created:` says only THAT a pass made it, where `enriched:` names which
		// pass, so the glyphs must not be the same one.
		expect(facetValueIcon('created', 'sift')).toBeDefined();
		expect(facetValueIcon('created', 'sift')).not.toBe(facetValueIcon('created', 'stashdb'));
		expect(facetValueIcon('created', 'me')).not.toBe(facetValueIcon('created', 'sift'));
		// And neither is a box, so neither takes a box's glyph.
		expect(boxIcon('sift')).toBeUndefined();
		expect(boxIcon('me')).toBeUndefined();
	});

	it('draws a glyph per value only on the two columns whose rows are authors', () => {
		/*
		 * Asked a stash-box wears no glyph per row: one mark repeated down a column says nothing
		 * its words do not, and the heading already wears it. A glyph per value is for Enriched by
		 * and Created by, where the mark is what tells authors apart.
		 */
		expect([...VALUE_GLYPH_FACETS].sort()).toEqual(['created', 'enriched']);
		for (const value of ['local', 'never', 'today', 'week', 'month', 'older']) {
			expect(facetValueIcon('enrichment', value), value).toBeUndefined();
			// The words stay: only the mark went.
			expect(facetValueLabel('enrichment', value), value).not.toBe(value);
		}
	});

	it('gives every way Sift itself enriches a file its own word and its own glyph', () => {
		/*
		 * Five authors, and each of the four that are Sift says which reading of the library it
		 * was: a face, a folder name, the file's own name, a watermark on the picture. The last two
		 * read the same table and are still two rows, because they disagree constantly in both
		 * directions and a reader must tell which wrote. Every row reads `<who>: <how>`. A value
		 * with no word falls through to the query language's own token (a bare `filename` on
		 * screen); a value with no glyph draws nothing, so the mark is missing on that row alone.
		 * Neither failure raises anything.
		 */
		const glyphs = new Set<string>();
		for (const [way, said] of [
			['faces', 'Sift: from a face'],
			['folder', 'Sift: from a folder name'],
			['filename', 'Sift: from a file name'],
			['watermark', 'Sift: from a watermark']
		]) {
			expect(facetValueLabel('enriched', way)).toBe(said);
			const glyph = facetValueIcon('enriched', way);
			expect(glyph).toBeDefined();
			glyphs.add(String(glyph));
		}
		// One each, or two authors are drawn as the same mark and the row says nothing new.
		expect(glyphs.size).toBe(4);
		expect(glyphs.has(String(facetValueIcon('enriched', 'stash')))).toBe(false);
	});

	it('keeps the name the server sent for an id, so a chip is not a ULID', () => {
		/* The panel is handed the name beside the count; the chip on the bar is drawn from the
		   address, which holds the id alone. Without this a tag picked on the People wall reads as
		   twenty-six characters of identifier on the most visible row in the application. */
		const id = '01ARZ3NDEKTSV4RRFFQ69G5FAV';
		expect(facetValueLabel('tags', id)).toBe(id);

		rememberFacetNames('tags', [{ value: id, count: 12, label: 'Beach' }]);

		expect(facetValueLabel('tags', id)).toBe('Beach');
	});
});

describe('the columns belong to the noun', () => {
	it('gives each noun its own list, in the order that noun declares', () => {
		expect(facetKeys('person').slice(0, 5)).toEqual([
			'gender',
			'hair_color',
			'eye_color',
			'ethnicity',
			'country'
		]);
		// `kind` is not a Sites column: almost no site record carries one, so it would be a single
		// row reading "Site" over the whole wall, and the server offers none. `linked` is not a
		// column: `enriched` answers the same question by which box rather than yes and no. It is
		// still a word the server takes, so it is still a key, last in each list and left out of
		// the columns below.
		expect(facetKeys('site')).toEqual([
			'parent',
			'tags',
			'enriched',
			'usernames',
			'cover',
			'created',
			'sharing',
			'disagrees',
			'linked'
		]);
		expect(facetKeys('tag')).toEqual([
			'category',
			'parent',
			'enriched',
			'cover',
			'created',
			'sharing',
			'disagrees',
			'linked'
		]);
		// A photo set has no owner column, so 'mine' is a question the server refuses for it. The
		// columns every wall's rows share come in one order on every wall: tags, a cover, the maker,
		// sharing.
		expect(facetKeys('collection')).toEqual(['mine', 'tags', 'cover', 'created', 'sharing']);
		expect(facetKeys('photo_set')).toEqual(['tags', 'cover', 'created', 'sharing']);
		expect(facetKeys('person').slice(-5)).toEqual([
			'cover',
			'created',
			'sharing',
			'disagrees',
			'linked'
		]);
		// The queue's Site, under the parameter its address already carried. The state is the
		// screen's tab strip and deliberately not a column: two controls for one filter.
		expect(facetKeys('download')).toEqual(['site']);
	});

	it('offers on a wall of files every facet a file has a value for', () => {
		/* Browse and Theater draw this one list whole: who made the file, the heart, a Loop, the
		   Photo Sets it is in and what Sift could not make for it are columns as Tags and Rating are. */
		const keys = facetKeys('asset');
		for (const key of ['created', 'fav', 'loops', 'photo_sets', 'left_out']) {
			expect(keys, key).toContain(key);
		}
		expect(facetLabel('created')).toBe('Created by');
		expect(facetLabel('parent', 'tag')).toBe('Part of');
		expect(facetLabel('parent', 'site')).toBe('Network');
	});

	it('reads who made a file in the words and the glyph of the Created by line', () => {
		expect(facetValueLabel('created', 'compress')).toBe('Sift: from a file it compressed');
		expect(facetValueLabel('created', 'edit')).toBe('Sift: from a file it edited');
		expect(facetValueLabel('created', 'download')).toBe('Sift: from a download');
		expect(facetValueLabel('created', 'swap')).toBe('Sift: from a swap');
		expect(facetValueLabel('created', 'library')).toBe('Sift: from a library folder');
		expect(facetValueIcon('created', 'compress')).toBe('compress');
		expect(facetValueIcon('created', 'edit')).toBe('design_services');
		expect(facetValueIcon('created', 'swap')).toBe('swap_horiz');
		// A person's, a Site's or a tag's makers keep their own words.
		expect(facetValueLabel('created', 'me')).toBe('Me');
		expect(facetValueLabel('fav', 'yes')).toBe('Favorite');
		expect(facetValueLabel('loops', 'none')).toBe('No Loops');
		expect(facetValueLabel('left_out', 'previews')).toBe('Hover previews');
	});

	it('opens a file wall on the five that were always first', () => {
		expect(facetKeys('asset').slice(0, 5)).toEqual(['media', 'tags', 'people', 'in', 'rating']);
	});

	it('offers file size as a column, in bands', () => {
		/* No two files are the same number of bytes, which is true of an EXACT size and not of a
		   band, so a band makes a column, as Duration and Resolution each are. */
		expect(facetKeys('asset')).toContain('size');
		expect(facetLabel('size')).toBe('File size');
	});

	it('declares a glyph for every facet of every noun', () => {
		/* The panel's chooser and the chips both draw one. A facet with none would be a blank space
		   in a column of marks rather than an obvious omission. */
		for (const noun of [
			'asset',
			'person',
			'site',
			'tag',
			'collection',
			'photo_set',
			'download'
		] as const) {
			for (const facet of facetsFor(noun)) {
				expect(facet.icon, `${noun}.${facet.key} has no glyph`).toBeTruthy();
			}
		}
	});

	it('counts each noun through its own route', () => {
		expect(facetRoute('asset')).toBe('/assets/facets');
		expect(facetRoute('person')).toBe('/people/facets');
		expect(facetRoute('site')).toBe('/sites/facets');
		expect(facetRoute('tag')).toBe('/tags/facets');
		expect(facetRoute('collection')).toBe('/collections/facets');
		expect(facetRoute('photo_set')).toBe('/photo-sets/facets');
		expect(facetRoute('download')).toBe('/downloads/facets');
	});

	it('spells a multi-value pick the way the route it is talking to reads it', () => {
		/* The file route takes a repeated key as "and" with a pipe inside one value for "or"; the
		   entity routes take a repeated key as "or" and know nothing about a pipe. Two grammars, and
		   the client writes whichever one it is addressing. */
		expect(picksAreRepeated('asset')).toBe(false);
		expect(picksAreRepeated('person')).toBe(true);
		expect(picksAreRepeated('collection')).toBe(true);
	});
});

describe('what the bar has narrowed a wall to', () => {
	it('reads this noun s facets and nothing else out of the address', () => {
		const address = new URLSearchParams(
			'hair_color=BLONDE&hair_color=RED&country=US&sort=largest&from=01ABC&offset=60'
		);

		expect(facetParams('person', address)).toEqual({
			hair_color: ['BLONDE', 'RED'],
			country: ['US']
		});
	});

	it('leaves out a key that belongs to a DIFFERENT noun', () => {
		/* `category` filters a tag and means nothing to a person. Passed through, it would reach the
		   list route as a parameter it ignores, and would draw a chip nothing on the wall answers. */
		expect(facetParams('person', new URLSearchParams('category=SCENE'))).toEqual({});
	});

	it('drops an empty value, which narrows nothing and would draw an empty chip', () => {
		expect(facetParams('tag', new URLSearchParams('category=&enriched=fansdb'))).toEqual({
			enriched: ['fansdb']
		});
	});

	it('still reads a key that was a column and is not one any more', () => {
		/*
		 * `enriched` replaced `linked` as a column of these three walls. A bookmarked `?linked=yes`
		 * must still be read, not dropped: dropped, it would come back as the whole wall, silently,
		 * which is worse than an error. It is one entry in the one list of keys, wearing `retired`,
		 * beside `admin` and `span`.
		 */
		expect(facetParams('person', new URLSearchParams('linked=yes'))).toEqual({
			linked: ['yes']
		});
		expect(facetParams('site', new URLSearchParams('linked=no'))).toEqual({
			linked: ['no']
		});
	});

	it('keeps the retired word out of the columns a panel offers', () => {
		/* The other half, and the half a reader would expect to be false: it filters, and there is
		   no row to tick. A column back on the panel would be two columns asking one question. */
		for (const noun of ['person', 'site', 'tag'] as const) {
			expect(facetKeys(noun)).toContain('linked');
			expect(facetsFor(noun).map((one) => one.key)).not.toContain('linked');
		}
	});

	it('says a retired value in words, because a chip has only the address to go on', () => {
		expect(facetLabel('linked')).toBe('Stash-box link');
		expect(facetValueLabel('linked', 'yes')).toBe('Enriched by a stash-box');
		expect(facetValueLabel('linked', 'no')).toBe('Not enriched');
	});
});

/*
 * ONE CLICK, ONE RULE: a thing reached through something else carries that something else with it.
 *
 * What is checked here is the two halves that make the rule work: which field names a kind of
 * thing on a wall of files, and what a wall asks for when its own constraint and the address both
 * name that field.
 */
describe('the field that names a kind of thing', () => {
	it("is the query language's own word for each kind with a page", () => {
		expect(fieldOf('person')).toBe('people');
		expect(fieldOf('tag')).toBe('tags');
		expect(fieldOf('site')).toBe('sites');
		expect(fieldOf('collection')).toBe('collections');
		expect(fieldOf('photo_set')).toBe('photo_sets');
		expect(fieldOf('song')).toBe('songs');
	});
});

describe('songs on the walls', () => {
	it('gives the Music wall its Artists column, the shared columns, and Sharing for an admin', () => {
		/* A song carries no tags of its own; who it credits is the column only it has, and its
		   sharing is an admin's column as on every other wall. */
		expect(facetsFor('song').map((one) => one.key)).toEqual([
			'artists',
			'cover',
			'created',
			'sharing'
		]);
		expect(facetsFor('song').find((one) => one.key === 'sharing')?.admin).toBe(true);
		expect(facetLabel('artists', 'song')).toBe('Artists');
		expect(facetRoute('song')).toBe('/songs/facets');
	});

	it('draws the Artists column with its own glyph, not the People one', () => {
		const artists = facetsFor('song').find((one) => one.key === 'artists');
		expect(artists?.icon).toBe('artist');
	});

	it('offers Music on the files wall in place of the retired column, which a link still reads', () => {
		/* The Music field is the song's name, kept so by the server, so two columns would be one
		   list twice. The retired word stays a key, so an address carrying it still narrows. */
		const columns = facetsFor('asset').map((one) => one.key);
		expect(columns).toContain('songs');
		expect(columns).not.toContain('music');
		expect(facetKeys('asset')).toContain('music');
		expect(facetLabel('songs', 'asset')).toBe('Music');
	});

	it('says AcoustID as a row of the Enriched by column, with the Music glyph', () => {
		expect(facetValueLabel('enriched', 'acoustid')).toBe('AcoustID: from the music');
		expect(facetValueIcon('enriched', 'acoustid')).toBe('music_note_2');
	});
});

describe('two narrowings on one wall', () => {
	it('asks for both where a file can carry several', () => {
		expect(bothNarrowings({ people: 'Jane Else' }, { people: 'Jane Doe' })).toEqual({
			people: ['Jane Doe', 'Jane Else']
		});
	});

	it('keeps an exclusion and a choice whole rather than gluing them together', () => {
		/* A comma joins two TEXTS: `-Jane Else` would become a person named "-Jane Else", and a pipe
		   list would be read as (mine and the first) or the second. A repeated parameter hands each
		   side to the parser as it was written. */
		expect(bothNarrowings({ people: '-Jane Else' }, { people: 'Jane Doe' }).people).toEqual([
			'Jane Doe',
			'-Jane Else'
		]);
		expect(bothNarrowings({ tags: 'beach|sunset' }, { tags: 'blue' }).tags).toEqual([
			'blue',
			'beach|sunset'
		]);
	});

	it('does not ask twice for a value both sides carry', () => {
		// Browse hands the address's own query back down as the screen's.
		expect(bothNarrowings({ tags: 'beach' }, { tags: 'beach' })).toEqual({ tags: 'beach' });
	});

	it('keeps every value of a name the address gave more than once', () => {
		expect(bothNarrowings({ tags: ['beach', 'sunset'] }, { tags: 'blue' }).tags).toEqual([
			'blue',
			'beach',
			'sunset'
		]);
	});

	it('lets the screen win a field a file has only one of', () => {
		/* A rating, a codec, whether something is a favourite: one value per file, so two at once is
		   nothing at all. Those keep the rule they have always had: the constraint that names the
		   screen wins. */
		expect(bothNarrowings({ fav: 'no' }, { fav: 'yes' })).toEqual({ fav: 'yes' });
		expect(bothNarrowings({ rating: '6' }, { rating: '8' })).toEqual({ rating: '8' });
	});

	it('leaves the address alone where the screen names nothing', () => {
		expect(bothNarrowings({ tags: 'beach', q: 'sunset' }, {})).toEqual({
			tags: 'beach',
			q: 'sunset'
		});
	});
});

describe('the files of one History line, as the chip says them', () => {
	/* A History line's count opens `?filed=<site>~<source>~<day>` (and `tagged`, `named`). The address
	   holds ids and a date; these are the words on the chip. */
	it('reads the group back off the address, a box name holding the separator included', () => {
		expect(readFiling('filed', 's1~download~2026-09-12')).toEqual({
			parameter: 'filed',
			subject: 's1',
			source: 'download',
			day: '2026-09-12',
			box: null
		});
		expect(readFiling('named', 'p1~stash_box~2026-09-12~odd~name')?.box).toBe('odd~name');
		expect(readFiling('tagged', 't1~~')).toMatchObject({ source: null, day: null });
	});

	it('refuses what is not a group, so the chip shows the address as it stands', () => {
		expect(readFiling('filed', 'garbage')).toBeNull();
		expect(readFiling('filed', 's1~download')).toBeNull();
		expect(readFiling('filed', '~download~2026-09-12')).toBeNull();
		expect(readFiling('filed', 's1~download~yesterday')).toBeNull();
	});

	it('says the thing, who did it and the day', () => {
		const line = readFiling('filed', 's1~download~2026-09-12')!;

		expect(`${filingField('filed')}: ${filingLabel(line, 'Instagram')}`).toBe(
			'filed under: Instagram by Sift on Sep 12, 2026'
		);
	});

	it('says who in the words the History uses for each source', () => {
		const byHand = readFiling('tagged', 't1~~2026-09-12')!;
		const byBox = readFiling('named', 'p1~stash_box~2026-09-12~fansdb')!;
		const byNoOneBox = readFiling('named', 'p1~stash_box~2026-09-12')!;

		expect(filingLabel(byHand, 'poolside')).toBe('poolside by hand on Sep 12, 2026');
		expect(filingLabel(byBox, 'Neve Alder')).toBe('Neve Alder by fansdb on Sep 12, 2026');
		expect(filingLabel(byNoOneBox, 'Neve Alder')).toBe('Neve Alder by a stash-box on Sep 12, 2026');
	});

	it('names the year every time, and says so where the line has no day', () => {
		expect(filingLabel(readFiling('filed', 's1~sift~2023-11-14')!, 'Instagram')).toBe(
			'Instagram by Sift on Nov 14, 2023'
		);
		expect(filingLabel(readFiling('filed', 's1~~')!, 'Instagram')).toBe(
			'Instagram by hand \u2014 no date recorded'
		);
	});

	it('reads the UTC day the line counted, not the local one', () => {
		/* The last minute of a UTC day is the next morning east of Greenwich and the previous evening
		   west of it; the chip says the day the History grouped by either way. */
		expect(filingLabel(readFiling('filed', 's1~sift~2026-01-01')!, 'Instagram')).toBe(
			'Instagram by Sift on Jan 1, 2026'
		);
	});

	it('says what the thing is rather than an id, where the viewer may not be told its name', () => {
		const line = readFiling('filed', 's1~sift~2026-09-12')!;

		expect(filingLabel(line, null)).toBe('a Site by Sift on Sep 12, 2026');
		expect(filingLabel({ ...line, parameter: 'tagged' }, null)).toBe(
			'a tag by Sift on Sep 12, 2026'
		);
		expect(filingLabel({ ...line, parameter: 'named' }, null)).toBe(
			'somebody by Sift on Sep 12, 2026'
		);
	});
});

describe('the Created by column on the walls of things', () => {
	it('names the task Sift made each by, as the thing page says it', () => {
		expect(facetValueLabel('created', 'facial_fingerprints')).toBe(
			'Sift: from facial fingerprints'
		);
		expect(facetValueLabel('created', 'folder')).toBe('Sift: from a folder name');
		expect(facetValueLabel('created', 'filename')).toBe('Sift: from a file name');
		expect(facetValueLabel('created', 'stash')).toBe('Sift: from a stash-box answer');
		// What Sift made before the task was recorded keeps its own row.
		expect(facetValueLabel('created', 'sift')).toBe('Sift');
	});
});
