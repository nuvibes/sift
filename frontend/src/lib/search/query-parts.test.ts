import { beforeEach, describe, expect, it, vi } from 'vitest';
import { asNamedFilters, partsOf, sameFilters, taken, typedParts, written } from './query-parts';
import type { ParsedClause, ParsedQuery } from '$lib/search/search.svelte';

/* The parser is the SERVER's and stays the server's: see the head of `query-parts.ts`. What is
   mocked here is the round trip to it, so the tests are about what this module does with an answer
   rather than about what the answer is. `search.svelte` is mocked whole, so `FIELDS` has to come
   back with it: `partsOf` reads it, and a mock that dropped it would make every test below pass by
   finding nothing. */
const answer = vi.hoisted(() => ({
	parsed: { text: '', clauses: [], terms: {}, problems: [] } as ParsedQuery
}));

vi.mock('$lib/search/search.svelte', async (original) => {
	const real = await original<typeof import('$lib/search/search.svelte')>();
	return { ...real, parseQuery: vi.fn(async () => answer.parsed) };
});

/** One clause as the server describes it. Defaults are the ordinary case: one field, one value. */
function clause(over: Partial<ParsedClause> & { query: string }): ParsedClause {
	return { field: null, values: [], negated: false, present: null, match: 'all', ...over };
}

/*
 * A named filter in an address, taken apart and put back together.
 *
 * One string carries three things at once, and every fault this pins is half of it being dropped:
 * the minus stuck to the first value, so `-Alice` is a different value from `Alice` and can be
 * added twice; a comma-joined list read as one long value that matches nothing anybody could click.
 * It is one pair because a SAVED filter is the same string in the same shape, and a second reader
 * would be that drift one file along.
 */

describe('taking one apart', () => {
	it('reads a single value', () => {
		expect(taken('Alice')).toEqual({ excluded: false, all: false, values: ['Alice'] });
	});

	it('takes the minus off rather than leaving it stuck to the first value', () => {
		/* The fault this exists for. Left on, `-Alice` is a different VALUE from `Alice`, so a row
		   that was already excluded could be added again and the column would show both. */
		expect(taken('-Alice')).toEqual({ excluded: true, all: false, values: ['Alice'] });
	});

	it('splits any-of on the pipe', () => {
		expect(taken('Alice|Bea')).toEqual({ excluded: false, all: false, values: ['Alice', 'Bea'] });
	});

	it('splits all-of on the comma, and remembers WHICH it was', () => {
		/* Reading only pipes would make an all-of column unclickable: the whole list would come back
		   as one value, so no row in the column matched it and none of them drew as chosen. */
		expect(taken('Alice,Bea')).toEqual({ excluded: false, all: true, values: ['Alice', 'Bea'] });
	});

	it('reads an excluded list', () => {
		expect(taken('-Alice|Bea')).toEqual({ excluded: true, all: false, values: ['Alice', 'Bea'] });
	});

	it('is not all-of on a single value, whichever separator was never there', () => {
		/* One value cannot be a combination, and calling it one would flip a column into all-of the
		   moment somebody ticked its second row. */
		expect(taken('Alice').all).toBe(false);
	});

	it('drops empty pieces rather than keeping a value with no name', () => {
		expect(taken('Alice||').values).toEqual(['Alice']);
		expect(taken('').values).toEqual([]);
	});
});

describe('writing one back', () => {
	it('is the inverse of reading it, for every shape', () => {
		for (const value of ['Alice', '-Alice', 'Alice|Bea', 'Alice,Bea', '-Alice|Bea', '-Alice,Bea']) {
			expect(written(taken(value)), value).toBe(value);
		}
	});

	it('spells the separator from what the pair carries rather than from the caller', () => {
		expect(written({ excluded: false, all: true, values: ['a', 'b'] })).toBe('a,b');
		expect(written({ excluded: true, all: false, values: ['a', 'b'] })).toBe('-a|b');
	});
});

describe('reading a whole saved query', () => {
	it('names every dimension it holds, in the order it was written', () => {
		const parts = partsOf('tags=PMV&sites=Pmvhaven');

		expect(parts.map((one) => one.field)).toEqual(['tags', 'sites']);
		expect(parts[0].values).toEqual(['PMV']);
	});

	it('leaves the CURSOR out, which is a position rather than something anybody chose', () => {
		/* Every kept filter carries the row it was saved at. Drawn, it would put an unreadable
		   identifier at the front of every list and it would be the longest thing there. */
		const parts = partsOf('from=01J5T6R7S8MNP6QRSTVWXYZ7A8&tags=PMV');

		expect(parts.map((one) => one.field)).toEqual(['tags']);
	});

	it('leaves the typed query out, because it has no dimension and no values to tick', () => {
		expect(partsOf('q=tags%3Abeach&tags=PMV').map((one) => one.field)).toEqual(['tags']);
	});

	it('leaves out anything the query language does not name', () => {
		/* A filter with no dimension cannot be drawn as a chip, and inventing one would put a
		   parameter somebody's own tooling added onto a screen as though Sift understood it. */
		expect(partsOf('utm_source=somewhere&tags=PMV').map((one) => one.field)).toEqual(['tags']);
	});

	it('carries the exclusion through, so a chip can be struck rather than lit', () => {
		const parts = partsOf('people=-Somebody');

		expect(parts[0].excluded).toBe(true);
		expect(parts[0].values).toEqual(['Somebody']);
	});

	it("reads a wall's OWN vocabulary when it is handed one", () => {
		/* Which names are filters is not the same question on every wall. `hair_color` is a dimension
		   of a person and no part of the file language, and `sort` is in that address too and is how
		   the wall is PAGED. Read in the file language, a People filter shows none of its own
		   dimensions; read in the wall's, it shows exactly them. */
		const parts = partsOf('hair_color=BLONDE&sort=largest', ['hair_color', 'gender']);

		expect(parts.map((one) => one.field)).toEqual(['hair_color']);
		expect(parts[0].values).toEqual(['BLONDE']);
	});

	it('finds nothing in an empty query rather than one nameless part', () => {
		expect(partsOf('')).toEqual([]);
	});
});

describe('moving what is typed into named filters', () => {
	beforeEach(() => {
		answer.parsed = { text: '', clauses: [], terms: {}, problems: [] };
	});

	it('leaves a query with no typed half exactly as it was', async () => {
		expect(await asNamedFilters('tags=beach&fav=yes')).toBe('tags=beach&fav=yes');
	});

	it('writes a typed filter as the parameter that means the same thing', async () => {
		/* The whole fault. A filter kept while `people:"Neve Arbogast"` is in the box would store it
		   in `q`, so applying it would put the words back in the SEARCH BAR, and nothing could show
		   what the filter holds, because what shows one reads named parameters. */
		answer.parsed = {
			text: '',
			terms: {},
			problems: [],
			clauses: [
				clause({ query: 'people:"Neve Arbogast"', field: 'people', values: ['Neve Arbogast'] })
			]
		};

		const moved = new URLSearchParams(await asNamedFilters('q=people%3A%22Neve+Arbogast%22'));
		expect(moved.get('people')).toBe('Neve Arbogast');
		expect(moved.get('q')).toBeNull();
	});

	it('carries the exclusion and the joiner through, so the filter still means what it meant', async () => {
		answer.parsed = {
			text: '',
			terms: {},
			problems: [],
			clauses: [
				clause({
					query: 'tags:runway OR tags:Edited',
					field: 'tags',
					values: ['runway', 'Edited'],
					match: 'any'
				}),
				clause({ query: '-people:Somebody', field: 'people', values: ['Somebody'], negated: true })
			]
		};

		const moved = new URLSearchParams(await asNamedFilters('q=whatever'));
		expect(moved.get('tags')).toBe('runway|Edited');
		expect(moved.get('people')).toBe('-Somebody');
	});

	it('keeps the free WORDS in the box, because words are what the box is for', async () => {
		answer.parsed = {
			text: 'beach party',
			terms: {},
			problems: [],
			clauses: [clause({ query: 'tags:runway', field: 'tags', values: ['runway'] })]
		};

		const moved = new URLSearchParams(await asNamedFilters('q=tags%3Arunway+beach+party'));
		expect(moved.get('tags')).toBe('runway');
		expect(moved.get('q')).toBe('beach party');
	});

	it('leaves a clause it cannot spell where the server wrote it', async () => {
		/* Three of them, and each is a real case rather than caution: a clause across two dimensions
		   has no name to be the parameter, a presence question has no values, and a value carrying a
		   separator needs QUOTING, which is the grammar, the one thing this client will not hold a
		   second copy of. */
		answer.parsed = {
			text: '',
			terms: {},
			problems: [],
			clauses: [
				clause({ query: 'tags:a OR people:b', field: null, values: ['a', 'b'], match: 'any' }),
				clause({ query: 'loops:any', field: 'loops', present: true }),
				clause({ query: 'tags:"a,b"', field: 'tags', values: ['a,b'] }),
				clause({ query: 'fav:yes', field: 'fav', values: ['yes'] })
			]
		};

		const moved = new URLSearchParams(await asNamedFilters('q=anything'));
		expect(moved.get('fav')).toBe('yes');
		expect(moved.get('q')).toBe('tags:a OR people:b loops:any tags:"a,b"');
	});

	it('adds to a dimension the address already names rather than replacing it', async () => {
		/* `set` would drop the one that was already there, which is the widening direction: the one
		   thing a filter may not do on its own. Two values of one parameter are ANDed by the server,
		   exactly as two clauses of one field in `q` were. */
		answer.parsed = {
			text: '',
			terms: {},
			problems: [],
			clauses: [clause({ query: 'tags:runway', field: 'tags', values: ['runway'] })]
		};

		const moved = new URLSearchParams(await asNamedFilters('tags=beach&q=tags%3Arunway'));
		expect(moved.getAll('tags')).toEqual(['beach', 'runway']);
	});

	it('changes nothing when the parser could not answer', async () => {
		/* `parseQuery` answers with the text and no clauses when the request fails. Nothing is moved,
		   and the filter goes on working exactly as it did, which beats a filter that quietly
		   loses half of itself because the network blinked. */
		answer.parsed = { text: 'tags:runway', clauses: [], terms: {}, problems: [] };

		expect(await asNamedFilters('q=tags%3Arunway')).toBe('q=tags%3Arunway');
	});
});

describe('recognising the screen as a filter somebody kept', () => {
	const held = (query: string) => partsOf(query);

	it('is true when the same filters are written in a different order', () => {
		/* A browser serialises parameters in whatever order they were set, so two spellings of one
		   filter are the ordinary case rather than the odd one. */
		expect(sameFilters(held('tags=runway&people=Anna'), held('people=Anna&tags=runway'))).toBe(
			true
		);
	});

	it('ignores the cursor and the typed words, because neither is a filter', () => {
		/* Every wall writes the row it settled at into its own address. Compared naively, a kept filter
		   would stop being recognised the moment somebody scrolled. */
		expect(
			sameFilters(held('from=01J5T6R7S8MNP6QRSTVWXYZ7A8&tags=runway'), held('tags=runway'))
		).toBe(true);
	});

	it('is false when one holds a filter the other does not', () => {
		expect(sameFilters(held('tags=runway'), held('tags=runway&fav=yes'))).toBe(false);
	});

	it('is false when a value differs, however slightly', () => {
		expect(sameFilters(held('tags=runway'), held('tags=Runway'))).toBe(false);
	});

	it('tells an exclusion from an inclusion', () => {
		/* The one that would be easy to lose: `written` puts the minus back, so the two spellings
		   differ. Dropped, a filter would be recognised as its own opposite. */
		expect(sameFilters(held('people=Anna'), held('people=-Anna'))).toBe(false);
	});

	it('tells any-of from all-of', () => {
		expect(sameFilters(held('tags=a|b'), held('tags=a,b'))).toBe(false);
	});

	it('does not call two values written the other way round the same filter', () => {
		/* `a|b` and `b|a` ARE the same set of files. They are not the same thing to READ, and this is
		   about recognising the filter somebody saved rather than about what it matches. */
		expect(sameFilters(held('tags=a|b'), held('tags=b|a'))).toBe(false);
	});

	it('finds nothing in common between two empty queries rather than calling them equal by accident', () => {
		// Both empty is genuinely equal; the CALLER is what refuses to name an unfiltered screen.
		expect(sameFilters(held(''), held(''))).toBe(true);
	});
});

describe('a query somebody typed', () => {
	/* The inverse of `savedQuery`, which is what writes this spelling. A saved WALL keeps each
	   cell's filter in it, and the bubble describing one must draw chips, as the bubble describing
	   a saved FILTER one screen over does, rather than the raw string as words. */
	const fields = ['people', 'sites', 'in', 'media', 'tags'];

	it('reads a token per filter, and quotes that hold a space', () => {
		const parts = typedParts('sites:Discord in:"Sift Downloads" media:video', fields);

		expect(parts).toEqual([
			{ field: 'sites', values: ['Discord'], all: false, excluded: false },
			{ field: 'in', values: ['Sift Downloads'], all: false, excluded: false },
			{ field: 'media', values: ['video'], all: false, excluded: false }
		]);
	});

	it('carries an exclusion, and gathers one field written twice', () => {
		const parts = typedParts('-tags:beach people:jane people:kim', fields);

		expect(parts[0]).toEqual({ field: 'tags', values: ['beach'], all: false, excluded: true });
		expect(parts[1].values).toEqual(['jane', 'kim']);
	});

	it('leaves out free words and fields this version does not know', () => {
		// Not guessed at: a token naming a field nothing here can draw is a chip saying something the
		// app cannot act on, and free words are a search rather than a filter.
		expect(typedParts('sunset cliff:high people:jane', fields)).toEqual([
			{ field: 'people', values: ['jane'], all: false, excluded: false }
		]);
	});
});
