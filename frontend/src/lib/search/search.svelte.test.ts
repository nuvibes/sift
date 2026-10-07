/* The client-side search logic that can actually be wrong.
 *
 * There is deliberately little here, and that is the point rather than a gap. The client does not
 * parse the query language and does not work out where a token starts. The server says both, so
 * there is no grammar here to test. What is left is splicing a chosen value into the box, and
 * keyboard navigation, and both have edges a person notices immediately.
 */

import { describe, expect, it } from 'vitest';
import {
	applyFilter,
	applySuggestion,
	chipLabel,
	chipPrefixLength,
	chipsFromParsed,
	chipText,
	dropToken,
	addressQuery,
	readAddress,
	rowFor,
	ruleText,
	sameChip,
	quoted,
	savedQuery,
	SearchBox,
	singleFor,
	serialise,
	storedQuery,
	type ParsedClause,
	type Remembered,
	type Suggestions
} from './search.svelte';

/** A search that was typed and run, as the box remembers one: the words identify it and the words
 *  are what the row shows. */
function ran(query: string): Remembered {
	return { kind: 'query', subject: query, label: query };
}

function offered(partial: Partial<Suggestions>): Suggestions {
	return {
		token: null,
		filters: [],
		matches: [],
		recent: [],
		replace_from: null,
		matched_from: null,
		for_query: '',
		...partial
	};
}

/**
 * One row of the dropdown, whole. The wire's row grows fields, and a fixture spelled out in full in
 * nine places is nine places a new field has to be typed into; written once, each test states only
 * what it is about.
 */
type Suggested = Suggestions['matches'][number];
function suggested(row: Partial<Suggested> & Pick<Suggested, 'value'>): Suggested {
	return {
		detail: null,
		count: null,
		opens: null,
		field: null,
		id: null,
		art: null,
		cover_asset_id: null,
		cover_upload_id: null,
		cover_at_ms: null,
		cover_frame: null,
		icon: null,
		...row
	};
}

/** One clause as the server sends it, with the parts a test does not care about filled in. */
function clause(over: Partial<ParsedClause> & { query: string }): ParsedClause {
	return { field: null, values: [], negated: false, present: null, match: 'all', ...over };
}

describe('splicing in the value that was picked', () => {
	it('replaces from where the server said the token starts', () => {
		expect(applySuggestion('people:ja', 'Jane Doe', 'people', 0)).toBe('people:"Jane Doe" ');
		expect(applySuggestion('tags:beach people:ja', 'Jane Doe', 'people', 11)).toBe(
			'tags:beach people:"Jane Doe" '
		);
	});

	it('keeps a value with a colon in it intact', () => {
		// The boundary comes from the server's offset, not `lastIndexOf(':')`, which would cut at
		// the colon INSIDE the value and rewrite `in:c:/photos` as `in:c:` plus the value. This is
		// the case that proves the offset is being used. No quotes: the value has no whitespace,
		// comma or quote in it, so it needs none, and the parser reads it back correctly because
		// a token splits on its FIRST colon.
		expect(applySuggestion('in:c:/photos', 'c:/photos/holiday', 'in', 0)).toBe(
			'in:c:/photos/holiday '
		);
	});

	it('does not quote a value that does not need it', () => {
		// A quote around every value would be noise, and would have to be typed by hand to
		// reproduce. The point of showing the completed query is that it can be retyped.
		expect(applySuggestion('tags:be', 'beach', 'tags', 0)).toBe('tags:beach ');
	});

	it('quotes a value containing a comma, because the modal splits on one', () => {
		// The two front-ends share a quoting rule. A tag genuinely called "beach, party" has to
		// survive being written either way, or the same value means two different things.
		expect(applySuggestion('tags:be', 'beach, party', 'tags', 0)).toBe('tags:"beach, party" ');
	});

	it('leaves no stray quote inside the quoted value', () => {
		// The parser reads an unbalanced quote as running to the end of the line, so emitting one
		// would swallow everything typed after it. The quotes are dropped rather than escaped,
		// because the language has no escape, which makes this a query for a name that does not
		// exist. That finds nothing, which is the safe direction to be wrong in.
		expect(applySuggestion('tags:x', 'a "b" c', 'tags', 0)).toBe('tags:"a b c" ');
	});

	it('refuses an offset that is outside the text rather than clamping it', () => {
		// The text can shrink between the server answering and the row being clicked. Clamping to
		// the end reads tidily and appends a SECOND copy of the token (`tags:tags:beach`). An
		// offset that cannot describe the text is not usable, so the box is left as it was.
		expect(applySuggestion('tags:', 'beach', 'tags', 999)).toBe('tags:');
		expect(applySuggestion('tags:', 'beach', 'tags', -1)).toBe('tags:');
	});
});

describe('refusing a completion aimed at text that has changed', () => {
	it('knows whether the rows on screen describe what is in the box', () => {
		const box = new SearchBox();
		box.suggestions = offered({ token: 'tags', replace_from: 0, for_query: 'tags:be' });

		expect(box.describes('tags:be')).toBe(true);
		// Four more characters typed while the answer was in flight. Completing here would rewrite
		// the line as though they had never been typed, deleting them with no undo.
		expect(box.describes('tags:be cat')).toBe(false);
	});
});

describe('the words themselves, offered as a row', () => {
	/*
	 * Once an entity row goes to that thing's PAGE, the dropdown has two kinds of answer on it and
	 * the difference cannot be left to an icon: typing a name that is somebody can mean "who is
	 * this" or "everything mentioning this", and both readings are reasonable. So the plain reading
	 * gets a row of its own. These are the rules for when it is there.
	 */
	function box(over: Partial<Suggestions>): SearchBox {
		const made = new SearchBox();
		made.suggestions = offered(over);
		return made;
	}

	const person = suggested({ value: 'Hollowgrain', count: 3, field: 'people', id: 'p1' });

	it('is first, so the row a hand lands on is what the box already did', () => {
		const rows = box({ for_query: 'hollowgrain', matches: [person] }).rows;

		expect(rows[0]).toEqual({ kind: 'text', query: 'hollowgrain' });
		expect(rows[1]?.kind).toBe('match');
	});

	it('is not offered inside a token, where every row completes the filter', () => {
		// `people:hollo` is one question and every row answers it. A row that ran `people:hollo` as a
		// phrase would be a different question wearing the same clothes.
		const rows = box({ token: 'people', for_query: 'people:hollo', matches: [person] }).rows;

		expect(rows.every((row) => row.kind !== 'text')).toBe(true);
	});

	it('is not offered with nothing typed, or with nothing to be confused with', () => {
		expect(box({ for_query: '', matches: [person] }).rows.some((r) => r.kind === 'text')).toBe(
			false
		);
		// No entity on the list means no second reading to distinguish, and a row that only repeats
		// what Enter does is a row that teaches nothing.
		expect(box({ for_query: 'hollowgrain', matches: [] }).rows.some((r) => r.kind === 'text')).toBe(
			false
		);
	});

	it('carries the id of the thing it offers, so picking it needs no second lookup', () => {
		// A name is not an identity: two people can share one, and names get edited. The row that
		// offered the person is the thing that knows which person it meant.
		const rows = box({ for_query: 'hollowgrain', matches: [person] }).rows;
		const match = rows.find((row) => row.kind === 'match');

		expect(match?.kind === 'match' && match.match.id).toBe('p1');
	});
});

describe('walking the dropdown with the arrow keys', () => {
	function box(rows: string[]): SearchBox {
		const made = new SearchBox();
		made.suggestions = offered({
			token: 'tags',
			matches: rows.map((value) => suggested({ value, field: 'tags' }))
		});
		return made;
	}

	it('starts at the top going down and at the bottom going up', () => {
		const down = box(['a', 'b', 'c']);
		down.move(1);
		expect(down.highlighted).toBe(0);

		const up = box(['a', 'b', 'c']);
		up.move(-1);
		expect(up.highlighted).toBe(2);
	});

	it('wraps back to nothing selected rather than to the far end', () => {
		// Arrowing off the bottom lands on what was typed, so the same key that got into the list
		// gets out of it. A list you can only leave with the mouse is one people leave with the
		// mouse, every time.
		const walking = box(['a', 'b']);
		walking.move(1);
		walking.move(1);
		walking.move(1);
		expect(walking.highlighted).toBe(-1);
	});

	it('does nothing when there is nothing to walk', () => {
		const empty = box([]);
		empty.move(1);
		expect(empty.highlighted).toBe(-1);
	});

	it('offers what the box remembers when no token is being typed', () => {
		const recent = new SearchBox();
		recent.suggestions = offered({ recent: [ran('beach'), ran('sunset')] });
		expect(recent.rows).toEqual([
			{ kind: 'recent', remembered: ran('beach') },
			{ kind: 'recent', remembered: ran('sunset') }
		]);
	});

	it('offers matches when one is', () => {
		expect(
			box(['beach']).rows.map((row) => (row.kind === 'match' ? row.match.value : null))
		).toEqual(['beach']);
	});

	it('offers matches from a bare word even with no single token', () => {
		// A bare word matches across the catalog: the dropdown has rows but no one token, and each
		// row carries the field it would complete to. The rows still come from the matches.
		const bare = new SearchBox();
		bare.suggestions = offered({
			token: null,
			replace_from: 0,
			matches: [
				suggested({ value: 'Hollis Danforth', count: 3, field: 'people' }),
				suggested({ value: 'holiday', count: 9, field: 'tags' })
			]
		});
		expect(bare.rows.map((row) => (row.kind === 'match' ? row.match.value : null))).toEqual([
			'Hollis Danforth',
			'holiday'
		]);
	});

	it('forgets what was highlighted when it is dismissed', () => {
		const open = box(['a']);
		open.open = true;
		open.move(1);
		open.dismiss();
		expect(open.open).toBe(false);
		expect(open.highlighted).toBe(-1);
	});
});

describe('putting a chosen filter in the box', () => {
	it('leaves the caret inside the token, with no trailing space', () => {
		/* The difference between finishing a thought and starting one. Completing a VALUE ends the
		 * token and the space says so; choosing a FILTER opens a question the dropdown is about to
		 * answer, and a space there would close the token before anything was said: the next
		 * keystroke would go off as free text. */
		expect(applyFilter('', 'tags', 0)).toBe('tags:');
		expect(applyFilter('peop', 'people', 0)).toBe('people:');
	});

	it('replaces the half-typed word rather than appending to it', () => {
		expect(applyFilter('tags:beach dur', 'duration', 11)).toBe('tags:beach duration:');
	});

	it('appends when there is no word being typed', () => {
		// An empty box, or a finished token and a space. The offset is the end of the line.
		expect(applyFilter('tags:beach ', 'people', 11)).toBe('tags:beach people:');
	});

	it('refuses an offset that is outside the text rather than clamping it', () => {
		// Same lesson as completing a value: clamping appends a second token nobody asked for.
		expect(applyFilter('tags:', 'tags', 999)).toBe('tags:');
		expect(applyFilter('tags:', 'tags', -1)).toBe('tags:');
	});
});

describe('taking the half-typed word off, once it has become a chip', () => {
	/* The box holds a chosen filter beside the text rather than inside it, so the word somebody was
	 * typing when they picked it has to come off. Otherwise `peo` sits in the box underneath a
	 * chip that already says `people:`. */

	it('removes the word the chip was made out of', () => {
		expect(dropToken('peo', 0)).toBe('');
		expect(dropToken('tags:beach dur', 11)).toBe('tags:beach ');
	});

	it('leaves a line with nothing being typed alone', () => {
		expect(dropToken('tags:beach ', 11)).toBe('tags:beach ');
	});

	it('refuses an offset outside the text rather than clamping it', () => {
		// Clamping would cut the line off somewhere nobody asked for. The same rule as the two
		// functions above, and the reason all three are written the same way.
		expect(dropToken('people', 999)).toBe('people');
		expect(dropToken('people', -1)).toBe('people');
	});
});

describe('the order the dropdown offers things in', () => {
	const FILTER = {
		field: 'tags',
		label: 'Tags',
		hint: 'Anything you have tagged',
		example: 'tags:beach',
		set_from: null
	};

	it('puts the library first, then the filters, then what was searched before', () => {
		/* One list because the arrow keys walk one list. The LIBRARY leads because what is in it is
		 * what somebody is looking for. The filters are the language documenting itself, which is
		 * worth having on the list and is a reference rather than an answer. Typing `du` to reach
		 * Nadia Vance and being shown `Duration` above her puts a lesson in front of the thing
		 * that was asked for. */
		const box = new SearchBox();
		box.suggestions = offered({
			filters: [FILTER],
			matches: [suggested({ value: 'beach', count: 4, field: 'tags' })],
			recent: [ran('sunset')]
		});

		expect(box.rows.map((row) => row.kind)).toEqual(['match', 'filter', 'recent']);
	});

	it('offers the filters on their own for an empty box', () => {
		// The case that matters most: clicking into an empty search box must show something, or it
		// is indistinguishable from a box that only does free text.
		const box = new SearchBox();
		box.suggestions = offered({ filters: [FILTER] });

		expect(box.rows).toEqual([{ kind: 'filter', filter: FILTER }]);
	});

	it('walks all three groups as one list', () => {
		const box = new SearchBox();
		box.suggestions = offered({
			filters: [FILTER],
			matches: [suggested({ value: 'beach', count: 4, field: 'tags' })],
			recent: [ran('sunset')]
		});

		box.move(1);
		expect(box.highlighted).toBe(0);
		box.move(1);
		box.move(1);
		expect(box.rows[box.highlighted].kind).toBe('recent');
	});
});

describe('a query held as chips and text', () => {
	const BEACH = { field: 'tags', value: 'beach' };
	const PARTY = { field: 'tags', value: 'beach party' };

	it('writes a chip the way the query language does', () => {
		expect(chipText(BEACH)).toBe('tags:beach');
		// The same quoting rule as completing a value, because it IS a value in a token, and the
		// whole reason a chip is worth having is that nobody has to type these quotes back.
		expect(chipText(PARTY)).toBe('tags:"beach party"');
	});

	it('puts the chips first and the typing after', () => {
		expect(serialise([BEACH], 'sun')).toBe('tags:beach sun');
		expect(serialise([BEACH, PARTY], '')).toBe('tags:beach tags:"beach party" ');
		expect(serialise([], 'sun')).toBe('sun');
		expect(serialise([], '')).toBe('');
	});

	it('measures the chips so an offset into the whole query can be moved into the text', () => {
		/* The load-bearing arithmetic. Every offset the server sends is into the serialised query,
		 * and the box splices into the text half, so the difference is this length exactly. One
		 * character out and a completion eats a character of what was typed, or leaves one behind. */
		const chips = [BEACH];
		const query = serialise(chips, 'sun');

		expect(query.slice(chipPrefixLength(chips))).toBe('sun');
	});

	it('measures nothing when there are no chips', () => {
		expect(chipPrefixLength([])).toBe(0);
	});

	it('measures a trailing chip including the space after it', () => {
		// With nothing typed yet the query still ends in a space, because the next thing typed is a
		// separate word rather than more of the last chip.
		const chips = [BEACH];
		expect(serialise(chips, '').length).toBe(chipPrefixLength(chips));
	});

	it('knows when two chips mean the same filter', () => {
		expect(sameChip(BEACH, { field: 'tags', value: 'beach' })).toBe(true);
		expect(sameChip(BEACH, { field: 'people', value: 'beach' })).toBe(false);
		expect(sameChip(BEACH, PARTY)).toBe(false);
	});

	it('takes chips and leftover text from what the parser made of a query', () => {
		/* Which half a piece of the query belongs in is the parser's decision, not the box's. That is
		 * what makes a typed query, a pasted link and a chip built by clicking all draw the same. */
		const split = chipsFromParsed({
			text: 'sunset',
			clauses: [
				clause({ query: 'tags:beach', field: 'tags', values: ['beach'] }),
				clause({ query: 'tags:party', field: 'tags', values: ['party'] }),
				clause({ query: 'people:"Jane Doe"', field: 'people', values: ['Jane Doe'] })
			],
			terms: {},
			problems: []
		});

		expect(split.text).toBe('sunset');
		expect(split.chips.map((chip) => chipText(chip))).toEqual([
			'tags:beach',
			'tags:party',
			'people:"Jane Doe"'
		]);
		expect(split.chips.every((chip) => chip.simple)).toBe(true);
	});

	it('round-trips a value that needed quoting', () => {
		// The server spelled it, so the quotes come back exactly as they went out and are never
		// something to strip off and put on again.
		const { chips } = chipsFromParsed({
			text: '',
			clauses: [clause({ query: 'tags:"beach party"', field: 'tags', values: ['beach party'] })],
			terms: {},
			problems: []
		});
		expect(serialise(chips, '')).toBe('tags:"beach party" ');
	});

	it('keeps a compound clause whole rather than splitting it into chips that all have to hold', () => {
		/* The reason a chip carries the server's own spelling. Built from the field and value alone,
		 * `tags:a OR tags:b` would have come back as two chips that BOTH had to match: a different
		 * search from the one that was typed, produced by drawing it. */
		const { chips } = chipsFromParsed({
			text: '',
			clauses: [
				clause({ query: 'tags:a OR tags:b', field: 'tags', values: ['a', 'b'], match: 'any' })
			],
			terms: {},
			problems: []
		});

		expect(chips).toHaveLength(1);
		expect(chipText(chips[0])).toBe('tags:a OR tags:b');
		expect(chips[0].value).toBe('a or b');
		expect(chips[0].simple).toBe(false);
	});

	it('labels an exclusion and a presence question as what they ask', () => {
		expect(
			chipLabel(clause({ query: '-tags:beach', field: 'tags', values: ['beach'], negated: true }))
		).toBe('not beach');
		expect(
			chipLabel(
				clause({ query: '-tags', field: 'tags', values: [], negated: true, present: false })
			)
		).toBe('none');
		expect(chipLabel(clause({ query: 'tags:any', field: 'tags', values: [], present: true }))).toBe(
			'any'
		);
	});
});

describe('saved searches', () => {
	/* There are two spellings of a stored search in the wild, and both have to keep working.
	 *
	 * The older ones are named parameters. A query with a choice in it has no spelling as a named
	 * parameter, so a search saved now carries the whole query instead. Reading both is the whole
	 * of the compatibility; nothing is rewritten behind them.
	 */
	it('reads a search saved as named parameters', () => {
		expect(savedQuery('tags=beach&type=video')).toBe('tags:beach type:video');
	});

	it('reads a search saved as a whole query', () => {
		expect(savedQuery(storedQuery('tags:a OR tags:b -people'))).toBe('tags:a OR tags:b -people');
	});

	it('quotes a stored value that would otherwise come apart', () => {
		expect(savedQuery('people=Jane+Doe')).toBe('people:"Jane Doe"');
	});

	it('keeps every value of a parameter given more than once', () => {
		// Dropping one would be fewer filters than were saved, which is the widening direction.
		expect(savedQuery('tags=beach&tags=sunset')).toBe('tags:beach tags:sunset');
	});

	it('reads a search that mixes the two spellings', () => {
		expect(savedQuery('q=tags%3Aa+OR+tags%3Ab&rating=4%2B')).toBe('tags:a OR tags:b rating:4+');
	});

	it('is nothing for a search that filters on nothing', () => {
		expect(savedQuery('')).toBe('');
	});

	it('puts a refusal on the token and quotes each value of a list on its own', () => {
		// Inside the quotes the minus would be part of a folder's name, which no folder has.
		expect(savedQuery('in=-Raw+Cuts')).toBe('-in:"Raw Cuts"');
		expect(savedQuery('people=Jane+Doe|Kim')).toBe('people:"Jane Doe"|Kim');
		expect(savedQuery('tags=a,b')).toBe('tags:a,b');
		expect(savedQuery('in="-Raw"')).toBe('in:"-Raw"');
	});

	it('quotes a value that begins with a minus or holds a pipe', () => {
		expect(quoted('-Raw')).toBe('"-Raw"');
		expect(quoted('a|b')).toBe('"a|b"');
		expect(quoted('a-b')).toBe('a-b');
	});

	it("reads the Same music strip's filter, which names a file by its ID", () => {
		/* The heading of the strip on the file page writes `same_music=<id>` into the address. A word
		   missing from the list is a filter that stops being read out of a saved search, so the
		   search quietly widens to the whole library. */
		expect(savedQuery('same_music=01HX0000000000000000000001')).toBe(
			'same_music:01HX0000000000000000000001'
		);
	});
});

describe('writing a rule as a query', () => {
	/* The Filters screen builds rows, and a row has to come out as something the parser reads back
	 * as the same rule. This is the only thing about the language the client decides, so it is the
	 * only thing that can drift from the parser, which is why it is out here and tested rather
	 * than sitting inside the screen. */
	it('writes any-of as a choice and all-of as a list', () => {
		expect(ruleText({ field: 'tags', how: 'any', values: 'a, b' })).toBe('tags:a OR tags:b');
		expect(ruleText({ field: 'tags', how: 'all', values: 'a, b' })).toBe('tags:a,b');
	});

	it('writes none-of as each one excluded', () => {
		expect(ruleText({ field: 'tags', how: 'none', values: 'a, b' })).toBe('-tags:a -tags:b');
	});

	it('writes the presence questions without values', () => {
		expect(ruleText({ field: 'people', how: 'empty', values: '' })).toBe('-people');
		expect(ruleText({ field: 'people', how: 'present', values: '' })).toBe('people:any');
	});

	it('quotes a value that would otherwise come apart', () => {
		expect(ruleText({ field: 'people', how: 'any', values: 'Jane Doe' })).toBe('people:"Jane Doe"');
		expect(ruleText({ field: 'tags', how: 'all', values: 'beach party, sunset' })).toBe(
			'tags:"beach party",sunset'
		);
	});

	it('is nothing at all for a row nobody filled in', () => {
		// Left out rather than written as a filter naming nothing, which would match no asset and
		// make a half-typed row empty the screen.
		expect(ruleText({ field: 'tags', how: 'any', values: '' })).toBe('');
		expect(ruleText({ field: 'tags', how: 'any', values: '  ,  ' })).toBe('');
	});
});

describe('what the Filters screen may take apart', () => {
	/* Applying rebuilds the whole query from what is on screen. So a clause this screen redraws
	 * WRONGLY, or does not redraw at all, is a filter changed or removed by the act of opening
	 * the screen and pressing Apply, with nothing on screen to say it happened.
	 *
	 * Each case below fails in the same direction: more gets through than was asked for. They are
	 * pinned as a group because the rule is one (write the row back out and compare), and a
	 * check per shape would leave the next shape to be found in use.
	 */
	it('takes a plain row apart', () => {
		const row = rowFor(
			clause({ query: 'tags:a OR tags:b', field: 'tags', values: ['a', 'b'], match: 'any' })
		);
		expect(row).toEqual({ field: 'tags', how: 'any', values: 'a, b' });
	});

	it('takes an exclusion and a presence question apart', () => {
		expect(
			rowFor(clause({ query: '-tags:beach', field: 'tags', values: ['beach'], negated: true }))
		).toEqual({ field: 'tags', how: 'none', values: 'beach' });
		expect(
			rowFor(clause({ query: '-people', field: 'people', negated: true, present: false }))
		).toEqual({ field: 'people', how: 'empty', values: '' });
	});

	it('refuses a choice of exclusions, which no row means', () => {
		// "not a OR not b" is not "none of a, b": the second keeps nothing carrying either, the
		// first keeps anything missing one of them. Drawn as a row it would have been filtered.
		expect(
			rowFor(
				clause({
					query: '-tags:a OR -tags:b',
					field: 'tags',
					values: ['a', 'b'],
					negated: true,
					match: 'all'
				})
			)
		).toBeNull();
	});

	it('refuses a list nested inside a choice, which a flat row would flatten', () => {
		// `tags:a,b OR tags:c` is "both a and b, or else c". As a row of "any of a, b, c" it would
		// have quietly become a wider search.
		expect(
			rowFor(
				clause({
					query: 'tags:c OR tags:a,b',
					field: 'tags',
					values: ['c', 'a', 'b'],
					match: 'any'
				})
			)
		).toBeNull();
	});

	it('refuses a choice that names no single field', () => {
		expect(
			rowFor(clause({ query: 'people:b OR tags:a', field: null, values: ['b', 'a'], match: 'any' }))
		).toBeNull();
	});

	it('lets a one-value control read a plain value or a presence question', () => {
		expect(singleFor(clause({ query: 'type:video', field: 'type', values: ['video'] }))).toBe(
			'video'
		);
		expect(
			singleFor(clause({ query: 'rating:none', field: 'rating', negated: true, present: false }))
		).toBe('none');
		expect(singleFor(clause({ query: 'rating:any', field: 'rating', present: true }))).toBe('any');
	});

	it('refuses to read an EXCLUDED value into a one-value control', () => {
		// There is no "not video" in a Kind list. Read as "video" it becomes its own opposite the
		// moment Apply is pressed, which is the worst of the three: the filter does not vanish, it
		// inverts, and the screen shows the inverted one as though it were what was asked for.
		expect(
			singleFor(clause({ query: '-type:video', field: 'type', values: ['video'], negated: true }))
		).toBeNull();
	});
});

describe('the query the top box reads out of an address', () => {
	/*
	 * A wall's own box (Search people, Search tags) keeps its words in `q`. The top box follows `q`,
	 * so what is typed into the wall's box would appear in the top box as well. Where the screen has a
	 * box of its own, the top box reads nothing from the address.
	 */
	it('reads nothing where the screen has a search box of its own', () => {
		expect(addressQuery(new URLSearchParams('q=nat'), true)).toBe('');
	});

	it('reads q where the top box is the only box on the screen', () => {
		expect(addressQuery(new URLSearchParams('q=nat'), false)).toBe('nat');
		expect(addressQuery(new URLSearchParams(''), false)).toBe('');
	});
});

describe('following the address back into the box', () => {
	/*
	 * The address is written by things that have nothing to do with the query (paging, the order,
	 * where the grid was left), and every one of them makes the box read it again. Compared against
	 * what the box HOLDS, a write that only moved the offset would find the query still empty while
	 * somebody was half way through typing, and empty the box under them.
	 */
	it('follows an address the box has not seen before', () => {
		expect(readAddress('tags:beach', null, '')).toBe('follow');
		expect(readAddress('tags:beach', 'tags:sunset', 'tags:sunset')).toBe('follow');
	});

	it('reads an empty address once, so an empty box is a thing it can say', () => {
		expect(readAddress('', null, '')).toBe('record');
		expect(readAddress('', '', 'tags:beach')).toBe('ignore');
	});

	it('leaves half-typed text alone when the address has not moved', () => {
		// The failure, stated as the arithmetic behind it. The address still says nothing about the
		// query, the box says `- tag`, and re-reading would throw the `- tag` away.
		expect(readAddress('', '', '- tag')).toBe('ignore');
		expect(readAddress('tags:beach', 'tags:beach', 'tags:beach and mo')).toBe('ignore');
	});

	it('writes down an address that agrees with the box rather than re-reading it', () => {
		// Nothing to re-read, and re-parsing would discard a half-finished filter for no reason.
		// But it still has to be written down, or the next read of the same address is a change.
		expect(readAddress('tags:beach', null, 'tags:beach')).toBe('record');
		expect(readAddress('tags:beach', null, '  tags:beach  ')).toBe('record');
	});
});

describe('how much of each band the dropdown draws', () => {
	/*
	 * Clicking into an empty box must not draw EVERY filter the language has and every search in
	 * the history: a wall of text over the page before a single letter has been typed. The list is
	 * supposed to converge as somebody types, and opening at full height says the opposite.
	 *
	 * So each band is cut to five with a row offering the rest. These are the rules for that row:
	 * where it appears, what it promises, and what pressing it does.
	 *
	 * The cut is in the STORE rather than in the markup on purpose, and that is what makes it
	 * testable here: `rows` is the list the arrow keys walk and the list `aria-activedescendant`
	 * names by index. A second, longer list in the component would disagree with it about what row
	 * seven is, and Enter would pick something other than what is under the highlight.
	 */
	function history(count: number): SearchBox {
		const made = new SearchBox();
		made.suggestions = offered({
			recent: Array.from({ length: count }, (_, at) => ran(`search ${at}`))
		});
		return made;
	}

	it('draws five of a band and offers the rest', () => {
		const box = history(12);
		const rows = box.rows;

		expect(rows.filter((row) => row.kind === 'recent')).toHaveLength(5);
		expect(rows.at(-1)).toEqual({ kind: 'more', group: 'recent', reveals: 5 });
	});

	it('offers nothing extra when the whole band already fits', () => {
		// Five is not "more than five". An offer to show more of a list that is all on screen is a
		// row that does nothing, which is worse than no row.
		expect(history(5).rows.some((row) => row.kind === 'more')).toBe(false);
		expect(history(1).rows.some((row) => row.kind === 'more')).toBe(false);
	});

	it('promises what it will actually show, not always five', () => {
		// Seven rows, five drawn, two held back. A row saying "Show 5 more" and then showing two is a
		// small lie about a small thing, and it is the kind nobody reports.
		expect(history(7).rows.at(-1)).toEqual({ kind: 'more', group: 'recent', reveals: 2 });
	});

	it('shows five more each time, and stops offering once there are none left', () => {
		const box = history(12);

		box.showMore('recent');
		expect(box.rows.filter((row) => row.kind === 'recent')).toHaveLength(10);
		expect(box.rows.at(-1)).toEqual({ kind: 'more', group: 'recent', reveals: 2 });

		box.showMore('recent');
		expect(box.rows.filter((row) => row.kind === 'recent')).toHaveLength(12);
		expect(box.rows.some((row) => row.kind === 'more')).toBe(false);
	});

	it('cuts each band on its own', () => {
		/* One counter per band, not one for the dropdown. Expanding the history must not also expand
		   the filters. They are different lists answering different questions, and somebody who
		   wanted more of one did not ask for more of the other. */
		const box = new SearchBox();
		box.suggestions = offered({
			recent: Array.from({ length: 9 }, (_, at) => ran(`search ${at}`)),
			filters: Array.from({ length: 9 }, (_, at) => ({
				field: `f${at}`,
				token: `f${at}:`,
				label: `Filter ${at}`,
				hint: '',
				example: '',
				set_from: null
			})) as Suggestions['filters']
		});

		box.showMore('recent');

		expect(box.rows.filter((row) => row.kind === 'recent')).toHaveLength(9);
		expect(box.rows.filter((row) => row.kind === 'filter')).toHaveLength(5);
	});

	it('goes back to five when the answer changes', () => {
		/* Every keystroke that reaches the server replaces the answer. Left expanded, a history
		   somebody had opened up would keep its full height under the NEXT query's five filters,
		   so the dropdown would grow as they typed, which is exactly backwards. */
		const box = history(12);
		box.showMore('recent');
		expect(box.rows.filter((row) => row.kind === 'recent')).toHaveLength(10);

		box.suggestions = offered({
			recent: Array.from({ length: 12 }, (_, at) => ran(`later ${at}`))
		});

		expect(box.rows.filter((row) => row.kind === 'recent')).toHaveLength(5);
	});
});
